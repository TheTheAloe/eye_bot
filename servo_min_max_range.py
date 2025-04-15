import time
import math
import smbus
import random

class PCA9685:
    __MODE1 = 0x00
    __PRESCALE = 0xFE
    __LED0_ON_L = 0x06
    __LED0_ON_H = 0x07
    __LED0_OFF_L = 0x08
    __LED0_OFF_H = 0x09

    def __init__(self, address=0x40):
        self.bus = smbus.SMBus(1)
        self.address = address
        self.write(self.__MODE1, 0x00)

    def write(self, reg, value):
        self.bus.write_byte_data(self.address, reg, value)

    def setPWMFreq(self, freq):
        prescaleval = 25000000.0 / 4096.0 / float(freq) - 1
        prescale = math.floor(prescaleval + 0.5)
        oldmode = self.bus.read_byte_data(self.address, self.__MODE1)
        self.write(self.__MODE1, oldmode & 0x7F | 0x10)
        self.write(self.__PRESCALE, int(prescale))
        self.write(self.__MODE1, oldmode)
        time.sleep(0.005)
        self.write(self.__MODE1, oldmode | 0x80)

    def setServoAngle(self, channel, angle):
        angle = max(0, min(180, angle))
        pulse = 500 + (angle / 180.0) * 2000
        pulse_length = int(pulse * 4096 / 20000)
        self.setPWM(channel, 0, pulse_length)

    def setPWM(self, channel, on, off):
        self.write(self.__LED0_ON_L + 4 * channel, on & 0xFF)
        self.write(self.__LED0_ON_H + 4 * channel, on >> 8)
        self.write(self.__LED0_OFF_L + 4 * channel, off & 0xFF)
        self.write(self.__LED0_OFF_H + 4 * channel, off >> 8)

# Initialize PCA9685
pwm = PCA9685()
pwm.setPWMFreq(50)

# Eye movement range
servo0_min, servo0_max = 30, 140  # horizontal (left-right)
servo1_min, servo1_max = 80, 90   # vertical (up-down)

blink_chance = 0.1  # 10% chance to blink every loop

while True:
    # Random Eye Movement
    angle0 = random.randint(servo0_min, servo0_max)
    angle1 = random.randint(servo1_min, servo1_max)
    pwm.setServoAngle(0, angle0)
    pwm.setServoAngle(1, angle1)

    # Random Blink
    if random.random() < blink_chance:
        pwm.setServoAngle(2, 100)  # Close eye
        pwm.setServoAngle(15, 80)
        time.sleep(0.3)
        pwm.setServoAngle(2, 170)  # Open eye
        pwm.setServoAngle(15, 0)
        time.sleep(0.2)

    time.sleep(1)
