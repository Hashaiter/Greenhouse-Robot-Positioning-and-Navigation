"""UART motion-module client. Python 3; pyserial is needed ONLY for real UART.

No arguments prints a frame and exits without touching hardware.
Example (all wheels lifted): python motion_link.py --port COM5 --a 20 --b 20 --seconds 2 --confirm-wheels-lifted
"""
import argparse
import time


def encode(payload):
    checksum = 0
    for byte in payload.encode('ascii'):
        checksum ^= byte
    return f'{payload}*{checksum:02X}\n'.encode('ascii')


def drive(a, b):
    if type(a) is not int or type(b) is not int or not (-30 <= a <= 30 and -30 <= b <= 30):
        raise ValueError('Duty percentages must be integers from -30 to +30')
    return encode(f'D,{a},{b}')


def decode_status(raw):
    frame = raw.decode('ascii').rstrip('\r\n')
    payload, checksum = frame.rsplit('*', 1)
    if encode(payload).decode('ascii').rstrip('\n') != frame:
        raise ValueError('Bad telemetry checksum')
    parts = payload.split(',')
    if parts[0] != 'S' or len(parts) != 10:
        raise ValueError('Not a status frame')
    keys = ('state', 'target_a', 'target_b', 'applied_a', 'applied_b',
            'uptime_ms', 'accepted', 'rejected', 'encoders_available')
    return dict(zip(keys, map(int, parts[1:])))


def decode_encoders(raw):
    frame = raw.decode('ascii').rstrip('\r\n')
    payload, checksum = frame.rsplit('*', 1)
    if encode(payload).decode('ascii').rstrip('\n') != frame:
        raise ValueError('Bad encoder telemetry checksum')
    parts = payload.split(',')
    if parts[0] != 'E' or len(parts) != 7:
        raise ValueError('Not an encoder frame')
    keys = ('count_a', 'count_b', 'counts_per_second_a', 'counts_per_second_b',
            'activity_mask', 'flags')
    return dict(zip(keys, map(int, parts[1:])))


def decode_speed(raw):
    frame = raw.decode('ascii').rstrip('\r\n')
    payload, checksum = frame.rsplit('*', 1)
    if encode(payload).decode('ascii').rstrip('\n') != frame:
        raise ValueError('Bad RPM telemetry checksum')
    parts = payload.split(',')
    if parts[0] != 'R' or len(parts) != 7:
        raise ValueError('Not an RPM frame')
    keys = ('rpm_milli_a', 'rpm_milli_b', 'counts_per_turn_milli_a',
            'counts_per_turn_milli_b', 'uptime_ms', 'flags')
    values = dict(zip(keys, map(int, parts[1:])))
    if values['counts_per_turn_milli_a'] <= 0 or values['counts_per_turn_milli_b'] <= 0:
        raise ValueError('Invalid wheel calibration')
    return {**values, 'rpm_a': values['rpm_milli_a'] / 1000.0,
            'rpm_b': values['rpm_milli_b'] / 1000.0,
            'counts_per_turn_a': values['counts_per_turn_milli_a'] / 1000.0,
            'counts_per_turn_b': values['counts_per_turn_milli_b'] / 1000.0}


def decode_telemetry(raw):
    if raw.startswith(b'R,'):
        return {'type': 'wheel_speed', **decode_speed(raw)}
    if raw.startswith(b'E,'):
        return {'type': 'encoders', **decode_encoders(raw)}
    return {'type': 'status', **decode_status(raw)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', help='3.3 V USB-UART or Raspberry Pi UART port, NOT ST-Link')
    parser.add_argument('--a', type=int, default=0)
    parser.add_argument('--b', type=int, default=0)
    parser.add_argument('--seconds', type=float, default=2.0)
    parser.add_argument('--confirm-wheels-lifted', action='store_true')
    args = parser.parse_args()
    frame = drive(args.a, args.b)
    if not 0 < args.seconds <= 10:
        parser.error('--seconds must be greater than 0 and at most 10')
    if not args.port:
        print(frame.decode('ascii'), end='')
        return
    if (args.a or args.b) and not args.confirm_wheels_lifted:
        parser.error('Lift all wheels and check power wiring, then add --confirm-wheels-lifted')
    try:
        import serial
    except ImportError:
        parser.error('Real UART requires pyserial: python -m pip install pyserial')
    with serial.Serial(args.port, 115200, timeout=0.03, write_timeout=0.1) as link:
        try:
            link.write(encode('STOP'))
            link.flush()
            link.reset_input_buffer()
            end = time.monotonic() + args.seconds
            next_send = time.monotonic()
            while time.monotonic() < end:
                now = time.monotonic()
                if now >= next_send:
                    link.write(frame)
                    next_send = now + 0.1
                raw = link.readline()
                if raw:
                    try:
                        print(decode_telemetry(raw))
                    except (ValueError, UnicodeError):
                        pass  # A partial telemetry line is not a valid measurement.
        finally:
            # Communication failure still relies on firmware's 500 ms timeout.
            try:
                link.write(encode('STOP'))
                link.flush()
            except (serial.SerialException, serial.SerialTimeoutException):
                pass


if __name__ == '__main__':
    main()
