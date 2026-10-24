#ifndef CHASSIS_ENCODER_H
#define CHASSIS_ENCODER_H

/* Pure counter arithmetic. Counts are quadrature x4, NOT wheel revolutions.
 * Raw signs depend on wiring and must be calibrated before closed-loop use. */
#include <stdint.h>
#include <limits.h>

#define ENCODER_SELFTEST_COUNT 16U
#define ENCODER_FLAG_GAP 1U
#define ENCODER_FLAG_SAT_A 2U
#define ENCODER_FLAG_SAT_B 4U

typedef struct {
  uint16_t previous[2];
  int32_t total[2], rate[2], delta[2];
  uint32_t tick, samples, activity, flags;
} EncoderTracker;

static int32_t Encoder_Delta(uint16_t current, uint16_t previous)
{
  uint32_t difference = (uint16_t)(current - previous);
  return difference < 32768U ? (int32_t)difference : (int32_t)difference - 65536;
}

static int32_t Encoder_Add(int32_t total, int32_t delta, uint32_t *flags, uint32_t flag)
{
  if (delta > 0 && total > INT32_MAX - delta) {
    *flags |= flag;
    return INT32_MAX;
  }
  if (delta < 0 && total < INT32_MIN - delta) {
    *flags |= flag;
    return INT32_MIN;
  }
  return total + delta;
}

static void Encoder_Init(EncoderTracker *tracker, uint32_t now, uint16_t a, uint16_t b)
{
  *tracker = (EncoderTracker){0};
  tracker->previous[0] = a;
  tracker->previous[1] = b;
  tracker->tick = now;
}

static int Encoder_Update(EncoderTracker *tracker, uint32_t now, uint16_t a, uint16_t b)
{
  uint32_t elapsed = now - tracker->tick;
  if (elapsed < 20U) return 0;
  uint16_t current[2] = {a, b};
  for (unsigned i = 0; i < 2U; ++i) {
    int32_t delta = Encoder_Delta(current[i], tracker->previous[i]);
    tracker->previous[i] = current[i];
    /* A long sampling gap can make modular direction ambiguous: discard it
     * and latch an invalid-data flag. Never invent a distance after a stall. */
    if (elapsed > 100U) delta = 0;
    tracker->delta[i] = delta;
    tracker->rate[i] = elapsed > 100U ? 0 : delta * 1000 / (int32_t)elapsed;
    tracker->total[i] = Encoder_Add(tracker->total[i], delta, &tracker->flags,
                                  i ? ENCODER_FLAG_SAT_B : ENCODER_FLAG_SAT_A);
    if (delta) tracker->activity |= 1U << i;
  }
  if (elapsed > 100U) tracker->flags |= ENCODER_FLAG_GAP;
  tracker->tick = now;
  ++tracker->samples;
  return 1;
}

static uint32_t Encoder_SelfTest(void)
{
  uint32_t failures = 0;
  EncoderTracker tracker;
#define ENCODER_CHECK(bit, condition) do { if (!(condition)) failures |= 1UL << (bit); } while (0)
  ENCODER_CHECK(0, Encoder_Delta(1, 1) == 0);
  ENCODER_CHECK(1, Encoder_Delta(102, 100) == 2);
  ENCODER_CHECK(2, Encoder_Delta(100, 102) == -2);
  ENCODER_CHECK(3, Encoder_Delta(1, 65534) == 3);
  ENCODER_CHECK(4, Encoder_Delta(65534, 1) == -3);
  ENCODER_CHECK(5, Encoder_Delta(32767, 0) == 32767);
  ENCODER_CHECK(6, Encoder_Delta(32768, 0) == -32768);
  Encoder_Init(&tracker, 0, 100, 100);
  ENCODER_CHECK(7, !Encoder_Update(&tracker, 19, 102, 98) && tracker.total[0] == 0);
  Encoder_Update(&tracker, 20, 102, 98);
  ENCODER_CHECK(8, tracker.total[0] == 2 && tracker.total[1] == -2 &&
                  tracker.rate[0] == 100 && tracker.rate[1] == -100 &&
                  tracker.activity == 3 && tracker.samples == 1);
  Encoder_Init(&tracker, UINT32_MAX - 9U, 65534, 1);
  Encoder_Update(&tracker, 10, 1, 65534);
  ENCODER_CHECK(9, tracker.total[0] == 3 && tracker.total[1] == -3 && tracker.flags == 0);
  Encoder_Init(&tracker, 0, 0, 0);
  Encoder_Update(&tracker, 101, 1000, 1000);
  ENCODER_CHECK(10, tracker.flags == ENCODER_FLAG_GAP && tracker.total[0] == 0 &&
                   tracker.rate[0] == 0 && tracker.previous[0] == 1000);
  Encoder_Update(&tracker, 121, 1001, 1001);
  ENCODER_CHECK(11, tracker.flags == ENCODER_FLAG_GAP && tracker.total[0] == 1);
  Encoder_Init(&tracker, 0, 0, 0);
  tracker.total[0] = INT32_MAX - 1;
  tracker.total[1] = INT32_MIN + 1;
  Encoder_Update(&tracker, 20, 2, 65534);
  ENCODER_CHECK(12, tracker.total[0] == INT32_MAX && (tracker.flags & ENCODER_FLAG_SAT_A));
  ENCODER_CHECK(13, tracker.total[1] == INT32_MIN && (tracker.flags & ENCODER_FLAG_SAT_B));
  Encoder_Update(&tracker, 40, 3, 65533);
  ENCODER_CHECK(14, tracker.total[0] == INT32_MAX && tracker.total[1] == INT32_MIN &&
                   tracker.flags == (ENCODER_FLAG_SAT_A | ENCODER_FLAG_SAT_B));
  Encoder_Init(&tracker, 100, 0, 0);
  Encoder_Update(&tracker, 121, 1, 0);
  ENCODER_CHECK(15, tracker.total[0] == 1 && tracker.rate[0] == 47 && tracker.activity == 1);
#undef ENCODER_CHECK
  return failures;
}
#endif
