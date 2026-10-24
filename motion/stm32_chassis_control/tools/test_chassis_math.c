/* Compile/run with a native C compiler. No board, commands or GPIO access. */
#include "../Core/Inc/chassis_control.h"
#include "../Core/Inc/chassis_encoder.h"
#include "../Core/Inc/chassis_speed.h"
#include <stdio.h>

int main(void)
{
  unsigned long control = Chassis_SelfTest();
  unsigned long encoder = Encoder_SelfTest();
  unsigned long speed = Speed_SelfTest();
  printf("control(26)=%lu encoder(16)=%lu speed(12)=%lu\n", control, encoder, speed);
  return (control || encoder || speed) ? 1 : 0;
}
