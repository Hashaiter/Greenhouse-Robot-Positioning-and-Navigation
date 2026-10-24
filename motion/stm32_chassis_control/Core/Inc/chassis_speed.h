#ifndef CHASSIS_SPEED_H
#define CHASSIS_SPEED_H

/* Manual wheel/output-shaft calibration, quadrature x4, 2026-10-11.
 * A: 15617 + 15573 counts / 20 turns = 1559.5 counts/turn.
 * B: 15698 + 15558 counts / 20 turns = 1562.8 counts/turn.
 * Estimates, not manufacturer specifications. Raw encoder data is unchanged.
 * Positive motor commands produced positive A and negative B raw counts. */
#include <stdint.h>
#include <limits.h>

#define SPEED_COUNTS_PER_TURN_MILLI_A 1559500U
#define SPEED_COUNTS_PER_TURN_MILLI_B 1562800U
#define SPEED_DIRECTION_A 1
#define SPEED_DIRECTION_B (-1)
#define SPEED_SELFTEST_COUNT 12U
#define SPEED_FLAG_SAT_A 8U
#define SPEED_FLAG_SAT_B 16U
#define SPEED_FLAG_CALIBRATION 32U

/* RPM is signed, stored as milli-RPM. No float printf or lost raw sign.
 * 64-bit intermediate also handles INT32_MIN without negation overflow.
 * Division truncates toward zero; precision is not measurement accuracy. */
static int32_t Speed_RpmMilli(int32_t counts_per_second, uint32_t cpr_milli,
                             int direction, uint32_t *flags, uint32_t sat_flag)
{
  if (!cpr_milli || (direction != 1 && direction != -1)) {
    *flags |= SPEED_FLAG_CALIBRATION;
    return 0;
  }
  int64_t value = (int64_t)counts_per_second * direction * 60000000LL / cpr_milli;
  if (value > INT32_MAX) {
    *flags |= sat_flag;
    return INT32_MAX;
  }
  if (value < INT32_MIN) {
    *flags |= sat_flag;
    return INT32_MIN;
  }
  return (int32_t)value;
}

static uint32_t Speed_SelfTest(void)
{
  uint32_t failures = 0, flags = 0;
#define SPEED_CHECK(bit, condition) do { if (!(condition)) failures |= 1UL << (bit); } while (0)
  SPEED_CHECK(0, Speed_RpmMilli(0, SPEED_COUNTS_PER_TURN_MILLI_A,
                               SPEED_DIRECTION_A, &flags, SPEED_FLAG_SAT_A) == 0);
  SPEED_CHECK(1, Speed_RpmMilli(1000, 1000000U, 1, &flags, SPEED_FLAG_SAT_A) == 60000);
  SPEED_CHECK(2, Speed_RpmMilli(-1000, 1000000U, 1, &flags, SPEED_FLAG_SAT_A) == -60000);
  SPEED_CHECK(3, Speed_RpmMilli(-1000, 1000000U, -1, &flags, SPEED_FLAG_SAT_B) == 60000);
  SPEED_CHECK(4, Speed_RpmMilli(15595, SPEED_COUNTS_PER_TURN_MILLI_A,
                               SPEED_DIRECTION_A, &flags, SPEED_FLAG_SAT_A) == 600000);
  SPEED_CHECK(5, Speed_RpmMilli(-15628, SPEED_COUNTS_PER_TURN_MILLI_B,
                               SPEED_DIRECTION_B, &flags, SPEED_FLAG_SAT_B) == 600000);
  SPEED_CHECK(6, Speed_RpmMilli(1, SPEED_COUNTS_PER_TURN_MILLI_A,
                               SPEED_DIRECTION_A, &flags, SPEED_FLAG_SAT_A) == 38 && flags == 0);
  SPEED_CHECK(7, Speed_RpmMilli(INT32_MIN, 60000000U, -1, &flags, SPEED_FLAG_SAT_B)
                   == INT32_MAX && flags == SPEED_FLAG_SAT_B);
  flags = 0;
  SPEED_CHECK(8, Speed_RpmMilli(INT32_MIN, 1U, 1, &flags, SPEED_FLAG_SAT_A)
                   == INT32_MIN && flags == SPEED_FLAG_SAT_A);
  flags = 0;
  SPEED_CHECK(9, Speed_RpmMilli(1, 0U, 1, &flags, SPEED_FLAG_SAT_A)
                   == 0 && flags == SPEED_FLAG_CALIBRATION);
  flags = 0;
  SPEED_CHECK(10, Speed_RpmMilli(1, 1000000U, 0, &flags, SPEED_FLAG_SAT_A)
                    == 0 && flags == SPEED_FLAG_CALIBRATION);
  flags = 1U; /* Existing encoder flags must survive conversion. */
  SPEED_CHECK(11, Speed_RpmMilli(-15628, SPEED_COUNTS_PER_TURN_MILLI_B,
                                SPEED_DIRECTION_B, &flags, SPEED_FLAG_SAT_B)
                    == 600000 && flags == 1U);
#undef SPEED_CHECK
  return failures;
}
#endif
