#ifndef CHASSIS_PORT_H
#define CHASSIS_PORT_H

/* Include in main.c only. TIM3 and USART1 are configured here inside USER CODE;
 * CubeMX must not independently enable these peripherals without refactoring.
 * Current HSI/HCLK/APB clocks are 8 MHz. Keep PA0/PB12 wiring unchanged. */
#include "chassis_control.h"
#include "chassis_encoder.h"
#include "chassis_speed.h"
#include <stdio.h>

#define CHASSIS_DEBUG_PREFIX 0x434D0000UL
#define CHASSIS_RX_SIZE 128U
#define CHASSIS_TX_SIZE 256U

typedef struct {
  uint32_t magic;
  uint32_t selftest_failures;
  uint32_t uptime_ms;
  uint32_t state;
  int32_t target_a;
  int32_t target_b;
  int32_t applied_a;
  int32_t applied_b;
  uint32_t accepted_frames;
  uint32_t rejected_frames;
  uint32_t uart_errors;
  uint32_t gpio_a;
  uint32_t gpio_b;
  uint32_t selftest_count;
} ChassisSnapshot;

/* Debugger mailbox: write a complete checksummed frame (without LF), then a
 * new request value: 0x434D0000 | sequence (1..65535). Ack echoes the request.
 * This is an explicitly requested command, NOT an automatic boot sequence. */
volatile char chassis_debug_frame[64];
volatile uint32_t chassis_debug_request;
volatile uint32_t chassis_debug_ack;
volatile ChassisSnapshot chassis_status;

typedef struct {
  uint32_t magic, sequence_begin, selftest_failures, selftest_count, uptime_ms;
  uint32_t raw_a, raw_b;
  int32_t total_a, total_b, counts_per_second_a, counts_per_second_b;
  int32_t delta_a, delta_b;
  uint32_t activity_mask, flags, samples, sequence_end;
} EncoderSnapshot;
volatile EncoderSnapshot chassis_encoder_status;
typedef struct {
  uint32_t magic, sequence_begin, selftest_failures, selftest_count, uptime_ms;
  int32_t rpm_milli_a, rpm_milli_b;
  uint32_t counts_per_turn_milli_a, counts_per_turn_milli_b;
  int32_t direction_a, direction_b;
  uint32_t flags, sequence_end;
} SpeedSnapshot;
volatile SpeedSnapshot chassis_speed_status;
static EncoderTracker encoders;

static ChassisControl chassis;
static volatile int32_t port_duty[2];
static volatile uint32_t port_heartbeat;
static volatile uint32_t port_fault;
static volatile uint32_t port_uart_errors;
static volatile uint16_t rx_head, rx_tail;
static volatile uint8_t rx_ring[CHASSIS_RX_SIZE];
static uint16_t tx_head, tx_tail;
static char tx_ring[CHASSIS_TX_SIZE];
static char rx_line[64];
static unsigned rx_length;
static int rx_discard;
static uint32_t rx_last_byte;
static uint32_t led_tick, status_tick;
static uint32_t good_frames, bad_frames;
static int status_requested;

static void Chassis_SpeedSnapshot(void)
{
  uint32_t flags = encoders.flags;
  int32_t a = Speed_RpmMilli(encoders.rate[0], SPEED_COUNTS_PER_TURN_MILLI_A,
                            SPEED_DIRECTION_A, &flags, SPEED_FLAG_SAT_A);
  int32_t b = Speed_RpmMilli(encoders.rate[1], SPEED_COUNTS_PER_TURN_MILLI_B,
                            SPEED_DIRECTION_B, &flags, SPEED_FLAG_SAT_B);
  uint32_t sequence = chassis_speed_status.sequence_begin + 2U;
  chassis_speed_status.sequence_begin = sequence | 1U;
  __DMB();
  chassis_speed_status.uptime_ms = encoders.tick;
  chassis_speed_status.rpm_milli_a = a;
  chassis_speed_status.rpm_milli_b = b;
  chassis_speed_status.counts_per_turn_milli_a = SPEED_COUNTS_PER_TURN_MILLI_A;
  chassis_speed_status.counts_per_turn_milli_b = SPEED_COUNTS_PER_TURN_MILLI_B;
  chassis_speed_status.direction_a = SPEED_DIRECTION_A;
  chassis_speed_status.direction_b = SPEED_DIRECTION_B;
  chassis_speed_status.flags = flags;
  __DMB();
  chassis_speed_status.sequence_end = sequence;
  __DMB();
  chassis_speed_status.sequence_begin = sequence;
}

static void Chassis_EncoderSnapshot(void)
{
  uint32_t sequence = chassis_encoder_status.sequence_begin + 2U;
  chassis_encoder_status.sequence_begin = sequence | 1U;
  __DMB();
  chassis_encoder_status.uptime_ms = encoders.tick;
  chassis_encoder_status.raw_a = encoders.previous[0];
  chassis_encoder_status.raw_b = encoders.previous[1];
  chassis_encoder_status.total_a = encoders.total[0];
  chassis_encoder_status.total_b = encoders.total[1];
  chassis_encoder_status.counts_per_second_a = encoders.rate[0];
  chassis_encoder_status.counts_per_second_b = encoders.rate[1];
  chassis_encoder_status.delta_a = encoders.delta[0];
  chassis_encoder_status.delta_b = encoders.delta[1];
  chassis_encoder_status.activity_mask = encoders.activity;
  chassis_encoder_status.flags = encoders.flags;
  chassis_encoder_status.samples = encoders.samples;
  __DMB();
  chassis_encoder_status.sequence_end = sequence;
  __DMB();
  chassis_encoder_status.sequence_begin = sequence;
}

static void Chassis_EncoderTimerInit(TIM_TypeDef *timer)
{
  timer->CR1 = 0;
  timer->CR2 = 0;
  timer->SMCR = 0;
  timer->DIER = 0; /* Hardware counting, no edge-per-interrupt load. */
  timer->CCER = 0;
  timer->PSC = 0;
  timer->ARR = 65535U;
  /* CH1/CH2 mapped to TI1/TI2; fDTS/32, eight stable samples per input. */
  timer->CCMR1 = TIM_CCMR1_CC1S_0 | TIM_CCMR1_CC2S_0 |
                (15U << TIM_CCMR1_IC1F_Pos) | (15U << TIM_CCMR1_IC2F_Pos);
  timer->CCMR2 = 0;
  timer->CCER = TIM_CCER_CC1E | TIM_CCER_CC2E;
  timer->SMCR = TIM_SMCR_SMS_0 | TIM_SMCR_SMS_1; /* Encoder mode 3, x4. */
  timer->EGR = TIM_EGR_UG;
  timer->SR = 0;
  timer->CNT = 0;
  timer->CR1 = TIM_CR1_CEN;
}

static void Chassis_EncoderInit(void)
{
  __HAL_RCC_AFIO_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_TIM2_CLK_ENABLE();
  __HAL_RCC_TIM4_CLK_ENABLE();
  __HAL_RCC_TIM2_FORCE_RESET();
  __HAL_RCC_TIM2_RELEASE_RESET();
  __HAL_RCC_TIM4_FORCE_RESET();
  __HAL_RCC_TIM4_RELEASE_RESET();
  /* SWJ_CFG has undefined readback: explicitly write NOJTAG, retaining SWD,
   * rather than copying those bits during a remap read/modify/write. */
  uint32_t remap = AFIO->MAPR & ~(AFIO_MAPR_SWJ_CFG | AFIO_MAPR_TIM2_REMAP |
                                 AFIO_MAPR_TIM4_REMAP);
  AFIO->MAPR = remap | AFIO_MAPR_SWJ_CFG_JTAGDISABLE |
                      AFIO_MAPR_TIM2_REMAP_PARTIALREMAP1;
  GPIO_InitTypeDef input = {0};
  input.Mode = GPIO_MODE_INPUT;
  input.Pull = GPIO_NOPULL; /* Required for FT input above VDD + 0.3 V. */
  input.Pin = GPIO_PIN_6 | GPIO_PIN_7 | GPIO_PIN_3;
  HAL_GPIO_Init(GPIOB, &input);
  input.Pin = GPIO_PIN_15;
  HAL_GPIO_Init(GPIOA, &input);
  Chassis_EncoderTimerInit(TIM4); /* E1A->PB6, E1B->PB7, motor A. */
  Chassis_EncoderTimerInit(TIM2); /* E2A->PA15, E2B->PB3, motor B. */
  DBGMCU->CR &= ~(DBGMCU_CR_DBG_TIM2_STOP | DBGMCU_CR_DBG_TIM4_STOP);
  Encoder_Init(&encoders, HAL_GetTick(), (uint16_t)TIM4->CNT, (uint16_t)TIM2->CNT);
  chassis_encoder_status.magic = 0x454E4331UL; /* ENC1, snapshot version. */
  Chassis_EncoderSnapshot();
  chassis_speed_status.magic = 0x52504D31UL; /* RPM1, independent versioned snapshot. */
  Chassis_SpeedSnapshot();
}

void Chassis_EmergencyStop(void)
{
  /* BSRR is atomic; STBY low first. Works even if a timer IRQ was interrupted. */
  port_fault = 1U;
  GPIOB->BSRR = (uint32_t)(STBY_Pin | PWMB_Pin | BIN1_Pin | BIN2_Pin) << 16;
  GPIOA->BSRR = (uint32_t)(PWMA_Pin | AIN1_Pin | AIN2_Pin) << 16;
}

static void Chassis_OutputOff(void)
{
  GPIOB->BSRR = (uint32_t)(STBY_Pin | PWMB_Pin | BIN1_Pin | BIN2_Pin) << 16;
  GPIOA->BSRR = (uint32_t)(PWMA_Pin | AIN1_Pin | AIN2_Pin) << 16;
}

void TIM3_IRQHandler(void)
{
  uint32_t flags = TIM3->SR;
  /* Clear only the observed timer flags (write zero to clear, one to preserve). */
  TIM3->SR = ~flags;
  if (port_fault || (uint32_t)(HAL_GetTick() - port_heartbeat) > 100U) {
    Chassis_EmergencyStop();
    return;
  }
  if (flags & TIM_SR_UIF) {
    int a = port_duty[0], b = port_duty[1];
    Chassis_OutputOff();
    if (!a && !b) return;
    /* 2026-10-24: A is the right wheel and needs inverted drive polarity.
       Keep B (left wheel) polarity and encoder/RPM signs unchanged. */
    GPIOA->BSRR = a > 0 ? AIN2_Pin : (a < 0 ? AIN1_Pin : 0U);
    GPIOB->BSRR = b > 0 ? BIN2_Pin : (b < 0 ? BIN1_Pin : 0U);
    unsigned width_a = (unsigned)(a < 0 ? -a : a) * 100U;
    unsigned width_b = (unsigned)(b < 0 ? -b : b) * 100U;
    TIM3->CCR1 = width_a ? width_a : 1U;
    TIM3->CCR2 = width_b ? width_b : 1U;
    /* If a long interrupt delayed us beyond the compare, skip that pulse. */
    unsigned elapsed = TIM3->CNT;
    GPIOB->BSRR = STBY_Pin;
    if (width_a && elapsed < width_a) GPIOA->BSRR = PWMA_Pin;
    if (width_b && elapsed < width_b) GPIOB->BSRR = PWMB_Pin;
    /* Old compare flags from the previous period must not cut the new pulse. */
    return;
  }
  if (flags & TIM_SR_CC1IF) GPIOA->BSRR = (uint32_t)PWMA_Pin << 16;
  if (flags & TIM_SR_CC2IF) GPIOB->BSRR = (uint32_t)PWMB_Pin << 16;
}

void USART1_IRQHandler(void)
{
  uint32_t flags = USART1->SR;
  if (flags & (USART_SR_RXNE | USART_SR_ORE | USART_SR_NE | USART_SR_FE | USART_SR_PE)) {
    uint8_t byte = (uint8_t)USART1->DR; /* SR then DR clears receive/error flags. */
    if (flags & (USART_SR_ORE | USART_SR_NE | USART_SR_FE | USART_SR_PE)) {
      ++port_uart_errors;
      Chassis_EmergencyStop();
      return;
    }
    uint16_t next = (uint16_t)((rx_head + 1U) % CHASSIS_RX_SIZE);
    if (next == rx_tail) {
      ++port_uart_errors;
      Chassis_EmergencyStop();
      return;
    }
    rx_ring[rx_head] = byte;
    __DMB();
    rx_head = next;
  }
}

static void Chassis_Publish(uint32_t now)
{
  uint32_t saved = __get_PRIMASK();
  __disable_irq();
  if (port_fault) {
    Chassis_Stop(&chassis, now, CHASSIS_BAD_FRAME);
    rx_tail = rx_head;
    rx_length = 0;
    rx_discard = 1; /* Resynchronise at next newline after a hardware error. */
    port_fault = 0;
  }
  port_duty[0] = chassis.applied[0];
  port_duty[1] = chassis.applied[1];
  port_heartbeat = now;
  if (!port_duty[0] && !port_duty[1]) Chassis_OutputOff();
  __set_PRIMASK(saved);
}

static void Chassis_ReceiveFrame(const char *frame, uint32_t now)
{
  ChassisCommand command = Chassis_Parse(frame);
  if (command.kind == CMD_INVALID) ++bad_frames;
  else ++good_frames;
  Chassis_Command(&chassis, command, now);
  /* STOP, invalid commands and zero outputs act now, not at next PWM period. */
  Chassis_Publish(now);
  status_requested = 1;
}

static void Chassis_ReadSerial(uint32_t now)
{
  /* Bound work per iteration; hostile serial traffic cannot starve control. */
  for (unsigned count = 0; count < 32U && rx_tail != rx_head; ++count) {
    uint8_t byte = rx_ring[rx_tail];
    rx_tail = (uint16_t)((rx_tail + 1U) % CHASSIS_RX_SIZE);
    rx_last_byte = now;
    if (byte == '\n') {
      if (!rx_discard) {
        if (rx_length && rx_line[rx_length - 1U] == '\r') --rx_length;
        rx_line[rx_length] = '\0';
        Chassis_ReceiveFrame(rx_line, now);
      }
      rx_length = 0;
      rx_discard = 0;
    } else if (!rx_discard) {
      if (rx_length >= 63U || (byte != '\r' && (byte < 32U || byte > 126U))) {
        ++bad_frames;
        Chassis_Stop(&chassis, now, CHASSIS_BAD_FRAME);
        Chassis_Publish(now);
        rx_length = 0;
        rx_discard = 1;
      } else rx_line[rx_length++] = (char)byte;
    }
  }
  if (rx_length && (uint32_t)(now - rx_last_byte) >= 100U) {
    ++bad_frames;
    Chassis_Stop(&chassis, now, CHASSIS_BAD_FRAME);
    Chassis_Publish(now);
    rx_length = 0;
    rx_discard = 1;
  }
}

static void Chassis_ReadDebugger(uint32_t now)
{
  uint32_t request = chassis_debug_request;
  if (request == 0U || request == chassis_debug_ack) return;
  if ((request & 0xffff0000UL) != CHASSIS_DEBUG_PREFIX || !(request & 0xffffUL)) {
    Chassis_ReceiveFrame("", now);
    chassis_debug_ack = request;
    return;
  }
  char frame[64];
  unsigned i;
  for (i = 0; i < 63U && chassis_debug_frame[i]; ++i) frame[i] = chassis_debug_frame[i];
  frame[i] = '\0';
  if (i == 63U) frame[0] = '\0';
  Chassis_ReceiveFrame(frame, now);
  __DMB();
  chassis_debug_ack = request;
}

static void Chassis_SendStatus(void)
{
  char payload[112], frame[116];
  int length = snprintf(payload, sizeof(payload), "S,%lu,%d,%d,%d,%d,%lu,%lu,%lu,%u",
      (unsigned long)chassis.state, chassis.target[0], chassis.target[1],
      chassis.applied[0], chassis.applied[1], (unsigned long)HAL_GetTick(),
      (unsigned long)good_frames, (unsigned long)bad_frames,
      (encoders.activity == 3U && encoders.flags == 0U) ? 1U : 0U);
  if (length < 0 || (unsigned)length >= sizeof(payload)) return;
  Chassis_Frame(payload, frame);
  size_t n = strlen(frame);
  unsigned free_bytes = (tx_tail + CHASSIS_TX_SIZE - tx_head - 1U) % CHASSIS_TX_SIZE;
  if (free_bytes < n + 1U) return; /* Drop entire frame, never half a frame. */
  for (size_t i = 0; i < n; ++i) {
    tx_ring[tx_head] = frame[i];
    tx_head = (uint16_t)((tx_head + 1U) % CHASSIS_TX_SIZE);
  }
  tx_ring[tx_head] = '\n';
  tx_head = (uint16_t)((tx_head + 1U) % CHASSIS_TX_SIZE);
}

static void Chassis_SendEncoders(void)
{
  char payload[112], frame[116];
  int length = snprintf(payload, sizeof(payload), "E,%ld,%ld,%ld,%ld,%lu,%lu",
      (long)encoders.total[0], (long)encoders.total[1],
      (long)encoders.rate[0], (long)encoders.rate[1],
      (unsigned long)encoders.activity, (unsigned long)encoders.flags);
  if (length < 0 || (unsigned)length >= sizeof(payload)) return;
  Chassis_Frame(payload, frame);
  size_t n = strlen(frame);
  unsigned free_bytes = (tx_tail + CHASSIS_TX_SIZE - tx_head - 1U) % CHASSIS_TX_SIZE;
  if (free_bytes < n + 1U) return;
  for (size_t i = 0; i < n; ++i) {
    tx_ring[tx_head] = frame[i];
    tx_head = (uint16_t)((tx_head + 1U) % CHASSIS_TX_SIZE);
  }
  tx_ring[tx_head] = '\n';
  tx_head = (uint16_t)((tx_head + 1U) % CHASSIS_TX_SIZE);
}

static void Chassis_SendSpeed(void)
{
  char payload[112], frame[116];
  int length = snprintf(payload, sizeof(payload), "R,%ld,%ld,%lu,%lu,%lu,%lu",
      (long)chassis_speed_status.rpm_milli_a, (long)chassis_speed_status.rpm_milli_b,
      (unsigned long)SPEED_COUNTS_PER_TURN_MILLI_A,
      (unsigned long)SPEED_COUNTS_PER_TURN_MILLI_B,
      (unsigned long)chassis_speed_status.uptime_ms,
      (unsigned long)chassis_speed_status.flags);
  if (length < 0 || (unsigned)length >= sizeof(payload)) return;
  Chassis_Frame(payload, frame);
  size_t n = strlen(frame);
  unsigned free_bytes = (tx_tail + CHASSIS_TX_SIZE - tx_head - 1U) % CHASSIS_TX_SIZE;
  if (free_bytes < n + 1U) return;
  for (size_t i = 0; i < n; ++i) {
    tx_ring[tx_head] = frame[i];
    tx_head = (uint16_t)((tx_head + 1U) % CHASSIS_TX_SIZE);
  }
  tx_ring[tx_head] = '\n';
  tx_head = (uint16_t)((tx_head + 1U) % CHASSIS_TX_SIZE);
}

static void Chassis_Snapshot(uint32_t now)
{
  /* Read snapshots twice if consistency is needed while the CPU is running. */
  chassis_status.uptime_ms = now;
  chassis_status.state = chassis.state;
  chassis_status.target_a = chassis.target[0];
  chassis_status.target_b = chassis.target[1];
  chassis_status.applied_a = chassis.applied[0];
  chassis_status.applied_b = chassis.applied[1];
  chassis_status.accepted_frames = good_frames;
  chassis_status.rejected_frames = bad_frames;
  chassis_status.uart_errors = port_uart_errors;
  chassis_status.gpio_a = GPIOA->ODR;
  chassis_status.gpio_b = GPIOB->ODR;
}

static void Chassis_PortInit(void)
{
  Chassis_OutputOff();
  Chassis_Init(&chassis, HAL_GetTick());
  chassis_status.magic = 0x43484153UL; /* CHAS */
  chassis_status.selftest_count = 26U;
  chassis_status.selftest_failures = Chassis_SelfTest();
  chassis_encoder_status.selftest_count = ENCODER_SELFTEST_COUNT;
  chassis_encoder_status.selftest_failures = Encoder_SelfTest();
  chassis_speed_status.selftest_count = SPEED_SELFTEST_COUNT;
  chassis_speed_status.selftest_failures = Speed_SelfTest();
  chassis_debug_request = 0;
  chassis_debug_ack = 0;
  if (chassis_status.selftest_failures || chassis_encoder_status.selftest_failures ||
      chassis_speed_status.selftest_failures) {
    chassis.state = CHASSIS_SELFTEST_FAULT;
    Chassis_Snapshot(HAL_GetTick());
    for (;;) {
      Chassis_OutputOff();
      HAL_GPIO_TogglePin(STATUS_LED_GPIO_Port, STATUS_LED_Pin);
      HAL_Delay(100U);
    }
  }
  /* Fail safe if CubeMX later changes the clocks without changing this port. */
  if (HAL_RCC_GetHCLKFreq() != 8000000U || HAL_RCC_GetPCLK1Freq() != 8000000U ||
      HAL_RCC_GetPCLK2Freq() != 8000000U) Error_Handler();

  Chassis_EncoderInit();

  GPIO_InitTypeDef pin = {0};
  __HAL_RCC_USART1_CLK_ENABLE();
  pin.Pin = GPIO_PIN_9;
  pin.Mode = GPIO_MODE_AF_PP;
  pin.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOA, &pin);
  pin.Pin = GPIO_PIN_10;
  pin.Mode = GPIO_MODE_INPUT;
  pin.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(GPIOA, &pin);
  USART1->CR1 = 0;
  USART1->CR2 = 0;
  USART1->CR3 = 0;
  USART1->BRR = (8000000U + 57600U) / 115200U;
  USART1->CR1 = USART_CR1_UE | USART_CR1_RE | USART_CR1_TE | USART_CR1_RXNEIE;
  HAL_NVIC_SetPriority(USART1_IRQn, 2, 0);
  HAL_NVIC_EnableIRQ(USART1_IRQn);

  /* Timer-assisted PWM: TIM3 runs internally, no hardware channel remapping.
   * Update starts each pulse; CC1/CC2 interrupts end PA0/PB12 independently. */
  __HAL_RCC_TIM3_CLK_ENABLE();
  TIM3->CR1 = 0;
  TIM3->PSC = 7U;
  TIM3->ARR = 9999U;
  TIM3->CCR1 = TIM3->CCR2 = 1U;
  TIM3->CCMR1 = 0;
  TIM3->CCER = 0;
  TIM3->EGR = TIM_EGR_UG;
  TIM3->SR = 0;
  TIM3->DIER = TIM_DIER_UIE | TIM_DIER_CC1IE | TIM_DIER_CC2IE;
  port_heartbeat = HAL_GetTick();
  HAL_NVIC_SetPriority(TIM3_IRQn, 1, 0);
  HAL_NVIC_EnableIRQ(TIM3_IRQn);
  TIM3->CR1 = TIM_CR1_CEN;

  /* Independent watchdog: a hung main loop resets to boot-idle (~1 s nominal).
   * Never halt the CPU with motor power connected: software PWM cannot turn
   * a pin off while halted. Do not freeze the watchdog, even during debugging. */
  DBGMCU->CR &= ~(DBGMCU_CR_DBG_IWDG_STOP | DBGMCU_CR_DBG_TIM3_STOP);
  IWDG->KR = 0xCCCCU; /* Start first: forces LSI on for register synchronisation. */
  IWDG->KR = 0x5555U;
  IWDG->PR = 4U; /* LSI / 64 */
  IWDG->RLR = 624U;
  uint32_t start = HAL_GetTick();
  while (IWDG->SR) if ((uint32_t)(HAL_GetTick() - start) > 100U) Error_Handler();
  IWDG->KR = 0xAAAAU;
  led_tick = status_tick = HAL_GetTick();
  Chassis_Publish(HAL_GetTick());
  Chassis_Snapshot(HAL_GetTick());
  status_requested = 1;
}

static void Chassis_PortPoll(void)
{
  uint32_t now = HAL_GetTick();
  if (Encoder_Update(&encoders, now, (uint16_t)TIM4->CNT, (uint16_t)TIM2->CNT)) {
    Chassis_EncoderSnapshot();
    Chassis_SpeedSnapshot();
  }
  Chassis_Publish(now); /* Consume any ISR fault before accepting more commands. */
  Chassis_ReadSerial(now);
  Chassis_ReadDebugger(now);
  Chassis_Update(&chassis, now);
  Chassis_Publish(now);
  uint32_t blink_ms = (chassis.state == CHASSIS_REMOTE || chassis.state == CHASSIS_DEMO)
                      ? 150U : (chassis.state >= CHASSIS_TIMEOUT ? 100U : 500U);
  if ((uint32_t)(now - led_tick) >= blink_ms) {
    led_tick = now;
    HAL_GPIO_TogglePin(STATUS_LED_GPIO_Port, STATUS_LED_Pin);
  }
  if (status_requested || (uint32_t)(now - status_tick) >= 250U) {
    status_tick = now;
    status_requested = 0;
    Chassis_SendStatus();
    Chassis_SendEncoders();
    Chassis_SendSpeed();
  }
  if (tx_tail != tx_head && (USART1->SR & USART_SR_TXE)) {
    USART1->DR = (uint8_t)tx_ring[tx_tail];
    tx_tail = (uint16_t)((tx_tail + 1U) % CHASSIS_TX_SIZE);
  }
  Chassis_Snapshot(now);
  IWDG->KR = 0xAAAAU;
}
#endif
