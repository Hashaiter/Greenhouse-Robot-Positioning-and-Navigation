#ifndef CHASSIS_CONTROL_H
#define CHASSIS_CONTROL_H

/* Pure control logic: no GPIO, interrupts, HAL delays, or encoder assumptions. */
#include <stdint.h>
#include <string.h>

#define CHASSIS_LIMIT 30
#define CHASSIS_TIMEOUT_MS 500U
#define CHASSIS_STEP_MS 20U
#define CHASSIS_REVERSE_MS 200U
#define CHASSIS_DEMO_MS 13000U

enum { CHASSIS_IDLE, CHASSIS_REMOTE, CHASSIS_DEMO,
       CHASSIS_TIMEOUT, CHASSIS_BAD_FRAME, CHASSIS_SELFTEST_FAULT };
enum { CMD_INVALID, CMD_DRIVE, CMD_STOP, CMD_STATUS, CMD_TEST };

typedef struct {
  int target[2];
  int applied[2];
  int last_sign[2];
  uint32_t zero_since[2];
  uint32_t last_step;
  uint32_t last_command;
  uint32_t demo_start;
  uint32_t state;
} ChassisControl;

typedef struct { int kind; int a; int b; } ChassisCommand;

static int Chassis_Sign(int value) { return (value > 0) - (value < 0); }

static void Chassis_Init(ChassisControl *c, uint32_t now)
{
  memset(c, 0, sizeof(*c));
  c->last_step = now;
  c->zero_since[0] = c->zero_since[1] = now;
}

static void Chassis_Stop(ChassisControl *c, uint32_t now, uint32_t reason)
{
  for (unsigned i = 0; i < 2; ++i) {
    if (c->applied[i] != 0) c->zero_since[i] = now;
    c->target[i] = c->applied[i] = 0;
  }
  c->state = reason;
}

static int Chassis_Hex(char ch)
{
  if (ch >= '0' && ch <= '9') return ch - '0';
  if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
  return -1;
}

/* Read a decimal percentage without accepting spaces, '+', or overflow. */
static int Chassis_Number(const char **cursor, int *out)
{
  const char *p = *cursor;
  int negative = (*p == '-');
  int value = 0;
  if (negative) ++p;
  if (*p < '0' || *p > '9') return 0;
  do {
    value = value * 10 + (*p++ - '0');
    if (value > CHASSIS_LIMIT) return 0;
  } while (*p >= '0' && *p <= '9');
  *out = negative ? -value : value;
  *cursor = p;
  return 1;
}

/* Input excludes CR/LF. Format: PAYLOAD*HH; HH = XOR of PAYLOAD bytes. */
static ChassisCommand Chassis_Parse(const char *frame)
{
  ChassisCommand cmd = {CMD_INVALID, 0, 0};
  size_t length = strlen(frame);
  if (length < 4U || length > 47U || frame[length - 3U] != '*') return cmd;
  int hi = Chassis_Hex(frame[length - 2U]);
  int lo = Chassis_Hex(frame[length - 1U]);
  if (hi < 0 || lo < 0) return cmd;
  uint8_t checksum = 0;
  for (size_t i = 0; i < length - 3U; ++i) checksum ^= (uint8_t)frame[i];
  if (checksum != (uint8_t)((hi << 4) | lo)) return cmd;
  if (length == 7U && memcmp(frame, "STOP", 4U) == 0) cmd.kind = CMD_STOP;
  else if (length == 9U && memcmp(frame, "STATUS", 6U) == 0) cmd.kind = CMD_STATUS;
  else if (length == 7U && memcmp(frame, "TEST", 4U) == 0) cmd.kind = CMD_TEST;
  else if (frame[0] == 'D' && frame[1] == ',') {
    const char *p = frame + 2;
    if (!Chassis_Number(&p, &cmd.a) || *p++ != ',' ||
        !Chassis_Number(&p, &cmd.b) || p != frame + length - 3U) return cmd;
    cmd.kind = CMD_DRIVE;
  }
  return cmd;
}

static void Chassis_Command(ChassisControl *c, ChassisCommand cmd, uint32_t now)
{
  switch (cmd.kind) {
  case CMD_DRIVE:
    /* Also enforce limits when called without the serial parser. */
    if (cmd.a < -CHASSIS_LIMIT || cmd.a > CHASSIS_LIMIT ||
        cmd.b < -CHASSIS_LIMIT || cmd.b > CHASSIS_LIMIT) {
      Chassis_Stop(c, now, CHASSIS_BAD_FRAME);
      break;
    }
    c->target[0] = cmd.a;
    c->target[1] = cmd.b;
    c->last_command = now;
    c->state = CHASSIS_REMOTE;
    break;
  case CMD_STOP: Chassis_Stop(c, now, CHASSIS_IDLE); break;
  case CMD_STATUS: break; /* Status requests MUST NOT renew the drive lease. */
  case CMD_TEST:
    /* Never restart an active test or enter it while remotely moving. */
    if (c->state == CHASSIS_REMOTE || c->state == CHASSIS_DEMO) break;
    Chassis_Stop(c, now, CHASSIS_DEMO);
    c->demo_start = now;
    break;
  default: Chassis_Stop(c, now, CHASSIS_BAD_FRAME); break;
  }
}

static void Chassis_Update(ChassisControl *c, uint32_t now)
{
  if (c->state == CHASSIS_REMOTE &&
      (uint32_t)(now - c->last_command) >= CHASSIS_TIMEOUT_MS) {
    Chassis_Stop(c, now, CHASSIS_TIMEOUT);
  }
  if (c->state == CHASSIS_DEMO) {
    uint32_t elapsed = (uint32_t)(now - c->demo_start);
    int a = 0, b = 0;
    if (elapsed >= CHASSIS_DEMO_MS) Chassis_Stop(c, now, CHASSIS_IDLE);
    else {
      if (elapsed >= 3000U && elapsed < 5000U) a = b = 20;
      else if (elapsed >= 7000U && elapsed < 9000U) a = b = -20;
      else if (elapsed >= 10000U && elapsed < 11000U) { a = -20; b = 20; }
      else if (elapsed >= 12000U) { a = 20; b = -20; }
      if (a == 0 && b == 0) Chassis_Stop(c, now, CHASSIS_DEMO);
      else { c->target[0] = a; c->target[1] = b; }
    }
  }
  if ((uint32_t)(now - c->last_step) < CHASSIS_STEP_MS) return;
  c->last_step = now; /* No catch-up jump after a stalled loop. */
  for (unsigned i = 0; i < 2; ++i) {
    int goal = c->target[i];
    int sign = Chassis_Sign(goal);
    if (c->applied[i] != 0 && Chassis_Sign(c->applied[i]) != sign) goal = 0;
    if (c->applied[i] == 0 && sign != 0 && c->last_sign[i] != 0 &&
        sign != c->last_sign[i] &&
        (uint32_t)(now - c->zero_since[i]) < CHASSIS_REVERSE_MS) continue;
    int previous = c->applied[i];
    if (previous < goal) ++c->applied[i];
    else if (previous > goal) --c->applied[i];
    if (previous != 0 && c->applied[i] == 0) c->zero_since[i] = now;
    if (c->applied[i] != 0) c->last_sign[i] = Chassis_Sign(c->applied[i]);
  }
}

/* Used both by telemetry and by tests to build real protocol frames. */
static void Chassis_Frame(const char *payload, char *output)
{
  static const char hex[] = "0123456789ABCDEF";
  uint8_t checksum = 0;
  while (*payload) { checksum ^= (uint8_t)*payload; *output++ = *payload++; }
  *output++ = '*';
  *output++ = hex[checksum >> 4];
  *output++ = hex[checksum & 15U];
  *output = '\0';
}

/* These tests execute the actual control/parser code on every boot, outputs OFF.
 * Each bit identifies an assertion; a nonzero result prevents motion entirely. */
static uint32_t Chassis_SelfTest(void)
{
  uint32_t failures = 0;
  ChassisControl c;
  ChassisCommand cmd;
  char frame[64];
#define CHECK(test, bit) do { if (!(test)) failures |= (1UL << (bit)); } while (0)
  Chassis_Init(&c, 0);
  Chassis_Update(&c, 5000);
  CHECK(c.state == CHASSIS_IDLE && !c.applied[0] && !c.applied[1], 0);
  Chassis_Frame("D,20,-30", frame); cmd = Chassis_Parse(frame);
  CHECK(cmd.kind == CMD_DRIVE && cmd.a == 20 && cmd.b == -30, 1);
  Chassis_Frame("D,31,0", frame); CHECK(Chassis_Parse(frame).kind == CMD_INVALID, 2);
  Chassis_Frame("D,9999999999999999999,0", frame);
  CHECK(Chassis_Parse(frame).kind == CMD_INVALID, 3);
  Chassis_Frame("D,2,3junk", frame); CHECK(Chassis_Parse(frame).kind == CMD_INVALID, 4);
  Chassis_Frame("D,-,3", frame); CHECK(Chassis_Parse(frame).kind == CMD_INVALID, 5);
  Chassis_Frame("D,20,20", frame); frame[2] = '3';
  CHECK(Chassis_Parse(frame).kind == CMD_INVALID, 6);
  CHECK(Chassis_Parse("").kind == CMD_INVALID &&
        Chassis_Parse("STOP*ZZ").kind == CMD_INVALID, 7);
  Chassis_Init(&c, 0); cmd = (ChassisCommand){CMD_DRIVE, 20, -20};
  Chassis_Command(&c, cmd, 0); Chassis_Update(&c, 20);
  CHECK(c.applied[0] == 1 && c.applied[1] == -1, 8);
  Chassis_Command(&c, (ChassisCommand){CMD_STATUS, 0, 0}, 499);
  Chassis_Update(&c, 500);
  CHECK(c.state == CHASSIS_TIMEOUT && !c.applied[0] && !c.applied[1], 9);
  Chassis_Init(&c, 0xfffffff0UL); Chassis_Command(&c, cmd, 0xfffffff0UL);
  Chassis_Update(&c, 484);
  CHECK(c.state == CHASSIS_TIMEOUT && !c.applied[0], 10);
  Chassis_Init(&c, 0); Chassis_Command(&c, cmd, 0);
  for (uint32_t t = 20; t <= 400; t += 20) Chassis_Update(&c, t);
  CHECK(c.applied[0] == 20 && c.applied[1] == -20, 11);
  Chassis_Command(&c, (ChassisCommand){CMD_DRIVE, -20, 20}, 400);
  for (uint32_t t = 420; t <= 800; t += 20) Chassis_Update(&c, t);
  CHECK(!c.applied[0] && !c.applied[1], 12);
  Chassis_Command(&c, (ChassisCommand){CMD_DRIVE, -20, 20}, 800);
  Chassis_Update(&c, 980); CHECK(!c.applied[0] && !c.applied[1], 13);
  Chassis_Update(&c, 1000); CHECK(c.applied[0] == -1 && c.applied[1] == 1, 14);
  Chassis_Command(&c, (ChassisCommand){CMD_STOP, 0, 0}, 1001);
  CHECK(c.state == CHASSIS_IDLE && !c.applied[0] && !c.target[1], 15);
  Chassis_Command(&c, (ChassisCommand){CMD_DRIVE, 1, -1}, 1002);
  Chassis_Update(&c, 1020); CHECK(!c.applied[0] && !c.applied[1], 16);
  Chassis_Command(&c, (ChassisCommand){CMD_DRIVE, 100, 0}, 1021);
  CHECK(c.state == CHASSIS_BAD_FRAME && !c.applied[0], 17);
  Chassis_Init(&c, 0); Chassis_Command(&c, (ChassisCommand){CMD_TEST, 0, 0}, 0);
  Chassis_Update(&c, 3000); CHECK(c.target[0] == 20 && c.target[1] == 20, 18);
  Chassis_Command(&c, (ChassisCommand){CMD_TEST, 0, 0}, 3100);
  CHECK(c.demo_start == 0, 19);
  Chassis_Update(&c, CHASSIS_DEMO_MS);
  CHECK(c.state == CHASSIS_IDLE && !c.applied[0] && !c.target[1], 20);
  Chassis_Frame("STOP", frame); CHECK(Chassis_Parse(frame).kind == CMD_STOP, 21);
  Chassis_Frame("STATUS", frame); CHECK(Chassis_Parse(frame).kind == CMD_STATUS, 22);
  Chassis_Frame("TEST", frame); CHECK(Chassis_Parse(frame).kind == CMD_TEST, 23);
  Chassis_Frame("D,1,2,3", frame); CHECK(Chassis_Parse(frame).kind == CMD_INVALID, 24);
  Chassis_Frame("D,+1,2", frame); CHECK(Chassis_Parse(frame).kind == CMD_INVALID, 25);
#undef CHECK
  return failures;
}
#endif
