"""Protocol utility tests: no serial hardware or third-party dependencies."""
import unittest
from motion_link import encode, drive, decode_status, decode_encoders, decode_speed, decode_telemetry


class MotionProtocolTests(unittest.TestCase):
    def test_documented_commands(self):
        self.assertEqual(encode('STOP'), b'STOP*18\n')
        self.assertEqual(encode('STATUS'), b'STATUS*14\n')
        self.assertEqual(encode('TEST'), b'TEST*16\n')
        self.assertEqual(drive(20, 20), b'D,20,20*44\n')
        self.assertEqual(drive(-20, 20), b'D,-20,20*69\n')

    def test_limits(self):
        for values in ((31, 0), (0, -31), (1.5, 0), (True, 0)):
            with self.assertRaises(ValueError):
                drive(*values)

    def test_status(self):
        status = decode_status(encode('S,1,20,-20,5,-5,1000,12,0,0'))
        self.assertEqual(status['state'], 1)
        self.assertEqual(status['applied_b'], -5)
        self.assertEqual(status['encoders_available'], 0)

    def test_reject_bad_status(self):
        for frame in (b'S,1,20,-20,5,-5,1000,12,0,0*00\n', encode('STOP'), b'junk'):
            with self.assertRaises(ValueError):
                decode_status(frame)

    def test_encoders(self):
        raw = encode('E,123,-456,100,-200,3,0')
        values = decode_encoders(raw)
        self.assertEqual(values['count_a'], 123)
        self.assertEqual(values['count_b'], -456)
        self.assertEqual(values['counts_per_second_b'], -200)
        self.assertEqual(values['activity_mask'], 3)
        self.assertEqual(decode_telemetry(raw)['type'], 'encoders')

    def test_reject_bad_encoders(self):
        for frame in (b'E,123,-456,100,-200,3,0*00\n', encode('E,1,2'),
                      encode('E,1,2,3,4,5,x'), encode('STOP')):
            with self.assertRaises(ValueError):
                decode_encoders(frame)

    def test_status_compatibility(self):
        values = decode_telemetry(encode('S,0,0,0,0,0,1000,0,0,1'))
        self.assertEqual(values['type'], 'status')
        self.assertEqual(values['encoders_available'], 1)

    def test_speed(self):
        raw = encode('R,51939,49936,1559500,1562800,1000,0')
        values = decode_speed(raw)
        self.assertEqual(values['rpm_a'], 51.939)
        self.assertEqual(values['rpm_b'], 49.936)
        self.assertEqual(values['counts_per_turn_a'], 1559.5)
        self.assertEqual(values['counts_per_turn_b'], 1562.8)
        self.assertEqual(decode_telemetry(raw)['type'], 'wheel_speed')

    def test_reverse_speed(self):
        values = decode_speed(encode('R,-60000,-60000,1559500,1562800,2000,1'))
        self.assertEqual(values['rpm_a'], -60)
        self.assertEqual(values['rpm_b'], -60)
        self.assertEqual(values['flags'], 1)

    def test_zero_speed(self):
        values = decode_speed(encode('R,0,0,1559500,1562800,3000,0'))
        self.assertEqual(values['rpm_a'], 0)
        self.assertEqual(values['rpm_b'], 0)

    def test_reject_bad_speed(self):
        for frame in (b'R,0,0,1559500,1562800,0,0*00\n', encode('R,1,2'),
                      encode('R,0,0,0,1562800,0,0'),
                      encode('R,0,0,1559500,-1,0,0'),
                      encode('R,0,0,1559500,1562800,0,x'), encode('STOP')):
            with self.assertRaises(ValueError):
                decode_speed(frame)


if __name__ == '__main__':
    unittest.main()
