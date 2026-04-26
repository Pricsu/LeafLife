/*
  ============================================================
  soil_rain_monitor.ino  — v5
  Dual HC-SR04 on servo (360° coverage) + TB6612FNG pump
  Arduino UNO R3
  ============================================================

  WIRING:
  ──────────────────────────────────────────────────────────
  Capacitive Soil Moisture v2.0
    VCC  -> 3.3V   WARNING: NOT 5V
    GND  -> GND
    AOUT -> A0

  Raindrop Module
    VCC  -> 5V
    GND  -> GND
    AO   -> A1

  HC-SR04  Sensor A (faces FRONT — 0°–180° arc)
    VCC  -> 5V
    GND  -> GND
    TRIG -> D8
    ECHO -> D9

  HC-SR04  Sensor B (faces BACK — 180°–360° arc)
    VCC  -> 5V
    GND  -> GND
    TRIG -> D12
    ECHO -> D13

  Servo Motor  (both sensors mounted 180° apart on the arm)
    Signal -> D11
    VCC    -> 5V external (servo draws too much for Arduino 5V)
    GND    -> GND

  TB6612FNG Motor Driver
    PWMA -> D3
    AIN1 -> D5
    AIN2 -> D4
    STBY -> D6
    VM   -> External power +
    VCC  -> 5V
    GND  -> GND + external GND

  360° COVERAGE EXPLAINED:
  ──────────────────────────────────────────────────────────
  Servo sweeps 15° -> 165° (one pass)
    Sensor A reads the FRONT arc:  servo angle + 0°
    Sensor B reads the BACK arc:   servo angle + 180°

  Example at servo = 90°:
    Sensor A covers 90°  (directly forward)
    Sensor B covers 270° (directly backward)
  Full sweep = complete 360° scan.
*/

#include <Servo.h>

// ── Sensor pins ───────────────────────────────
const uint8_t PIN_SOIL   = A0;
const uint8_t PIN_RAIN   = A1;

// ── Ultrasonic A (front) ──────────────────────
const uint8_t TRIG_A     = 8;
const uint8_t ECHO_A     = 9;

// ── Ultrasonic B (back — 180° opposite) ───────
const uint8_t TRIG_B     = 12;
const uint8_t ECHO_B     = 13;

// ── Servo ─────────────────────────────────────
const uint8_t SERVO_PIN  = 11;

// ── TB6612FNG ─────────────────────────────────
const uint8_t PWMA       = 3;
const uint8_t AIN1       = 5;
const uint8_t AIN2       = 4;
const uint8_t STBY       = 6;

// ── Config ────────────────────────────────────
const uint8_t  MOTOR_SPEED     = 220;
const uint32_t MAX_DURATION_MS = 30000;
const float    OBSTACLE_CM     = 15.0;   // testing range — change to 50.0 for production
const uint8_t  SWEEP_START     = 15;
const uint8_t  SWEEP_END       = 165;
const uint8_t  SWEEP_STEP      = 5;
const uint16_t SWEEP_DELAY_MS  = 15;
const uint16_t SAMPLES         = 10;
const uint16_t SAMPLE_DELAY    = 10;
const uint32_t PUBLISH_EVERY   = 2000;

Servo    myServo;
uint32_t lastPublish = 0;
bool     pumpRunning = false;
int      servoAngle  = SWEEP_START;
int      sweepDir    = 1;


// ══════════════════════════════════════════════
//  ULTRASONIC — measure one sensor by trig/echo pins
// ══════════════════════════════════════════════
int measureCm(uint8_t trig, uint8_t echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  long dur = pulseIn(echo, HIGH, 25000);  // 25ms timeout
  if (dur == 0) return 0;                 // 0 = no echo = very far = clear
  return dur * 0.034 / 2;
}

// ── Measure BOTH sensors and report over Serial ──
// Returns true if BOTH are clear (no obstacle on either arc)
bool measureBoth(int servoPos, bool report) {
  int distA = measureCm(TRIG_A, ECHO_A);
  delay(5);
  int distB = measureCm(TRIG_B, ECHO_B);

  // Real-world angles covered by each sensor
  int angleA = servoPos;             // front arc  (0°–180°)
  int angleB = servoPos + 180;       // back arc   (180°–360°)

  bool clearA = (distA == 0 || distA > OBSTACLE_CM);
  bool clearB = (distB == 0 || distB > OBSTACLE_CM);

  if (report) {
    // Report sensor A
    Serial.print(F("{\"type\":\"ultrasonic\",\"sensor\":\"A\",\"angle\":"));
    Serial.print(angleA);
    Serial.print(F(",\"dist_cm\":"));
    Serial.print(distA);
    Serial.print(F(",\"clear\":"));
    Serial.println(clearA ? F("true}") : F("false}"));

    // Report sensor B
    Serial.print(F("{\"type\":\"ultrasonic\",\"sensor\":\"B\",\"angle\":"));
    Serial.print(angleB);
    Serial.print(F(",\"dist_cm\":"));
    Serial.print(distB);
    Serial.print(F(",\"clear\":"));
    Serial.println(clearB ? F("true}") : F("false}"));
  }

  return clearA && clearB;
}


// ══════════════════════════════════════════════
//  SERVO
// ══════════════════════════════════════════════
void servoHome() {
  for (int a = servoAngle; a >= SWEEP_START; a -= 3) {
    myServo.write(a);
    delay(8);
  }
  servoAngle = SWEEP_START;
  sweepDir   = 1;
}


// ══════════════════════════════════════════════
//  PRE-CHECK: full 360° sweep before starting pump
//  Returns true = all clear, false = obstacle found
// ══════════════════════════════════════════════
bool sweepAndCheck() {
  // Forward 15->165
  for (int i = SWEEP_START; i <= SWEEP_END; i += SWEEP_STEP) {
    myServo.write(i);
    servoAngle = i;
    delay(SWEEP_DELAY_MS);

    if (!measureBoth(i, true)) {
      servoHome();
      return false;
    }
  }

  // Reverse 165->15
  for (int i = SWEEP_END; i >= SWEEP_START; i -= SWEEP_STEP) {
    myServo.write(i);
    servoAngle = i;
    delay(SWEEP_DELAY_MS);

    if (!measureBoth(i, true)) {
      servoHome();
      return false;
    }
  }

  servoHome();
  return true;
}


// ══════════════════════════════════════════════
//  MOTOR
// ══════════════════════════════════════════════
void motorStart() {
  digitalWrite(STBY, HIGH);
  digitalWrite(AIN1, HIGH);
  digitalWrite(AIN2, LOW);
  analogWrite(PWMA, MOTOR_SPEED);
  pumpRunning = true;
}

void motorStop() {
  analogWrite(PWMA, 0);
  digitalWrite(AIN1, LOW);
  digitalWrite(AIN2, LOW);
  digitalWrite(STBY, LOW);
  pumpRunning = false;
}


// ══════════════════════════════════════════════
//  PUMP + CONTINUOUS 360° MONITORING
// ══════════════════════════════════════════════
void runPumpWithMonitoring(float durationSec) {
  uint32_t durationMs = (uint32_t)(durationSec * 1000);
  uint32_t startTime  = millis();
  bool     aborted    = false;
  int      abortAngle = 0;

  motorStart();
  servoAngle = SWEEP_START;
  sweepDir   = 1;

  while (millis() - startTime < durationMs) {
    myServo.write(servoAngle);
    delay(SWEEP_DELAY_MS);

    bool clear = measureBoth(servoAngle, true);

    if (!clear) {
      motorStop();
      aborted    = true;
      abortAngle = servoAngle;
      break;
    }

    // Advance sweep
    servoAngle += SWEEP_STEP * sweepDir;
    if (servoAngle >= SWEEP_END)   { servoAngle = SWEEP_END;   sweepDir = -1; }
    if (servoAngle <= SWEEP_START) { servoAngle = SWEEP_START; sweepDir =  1; }
  }

  motorStop();
  servoHome();

  if (aborted) {
    Serial.print(F("{\"type\":\"pump\",\"status\":\"ABORTED\","
                   "\"reason\":\"obstacle\",\"angle\":"));
    Serial.print(abortAngle);
    Serial.println(F("}"));
  } else {
    Serial.print(F("{\"type\":\"pump\",\"status\":\"DONE\",\"duration\":"));
    Serial.print(durationSec, 1);
    Serial.println(F("}"));
  }
}


// ══════════════════════════════════════════════
//  HANDLE COMMAND FROM PYTHON
// ══════════════════════════════════════════════
void handleCommand(String line) {
  line.trim();
  if (line.length() == 0)              return;
  if (line.indexOf("\"START\"") == -1) return;

  int idx = line.indexOf("\"duration\":");
  if (idx == -1) return;

  int valStart = idx + 11;
  while (valStart < (int)line.length() && line[valStart] == ' ') valStart++;
  int valEnd = valStart;
  while (valEnd < (int)line.length() &&
        (isDigit(line[valEnd]) || line[valEnd] == '.')) valEnd++;

  float    durationSec = line.substring(valStart, valEnd).toFloat();
  uint32_t durationMs  = (uint32_t)(durationSec * 1000);

  if (durationMs < 500 || durationMs > MAX_DURATION_MS) {
    Serial.println(F("{\"type\":\"pump\",\"status\":\"ERROR\","
                     "\"msg\":\"duration out of range\"}"));
    return;
  }

  // Phase 1: 360° pre-check
  Serial.println(F("{\"type\":\"pump\",\"status\":\"SCANNING\"}"));
  bool clear = sweepAndCheck();

  if (!clear) {
    Serial.println(F("{\"type\":\"pump\",\"status\":\"BLOCKED\","
                     "\"reason\":\"obstacle in 360 pre-check\"}"));
    return;
  }

  // Phase 2: pump + continuous 360° monitoring
  Serial.print(F("{\"type\":\"pump\",\"status\":\"RUNNING\",\"duration\":"));
  Serial.print(durationSec, 1);
  Serial.println(F("}"));

  runPumpWithMonitoring(durationSec);
}


// ══════════════════════════════════════════════
//  ADC AVERAGE
// ══════════════════════════════════════════════
int readAverage(uint8_t pin) {
  long sum = 0;
  for (uint16_t i = 0; i < SAMPLES; i++) {
    sum += analogRead(pin);
    delay(SAMPLE_DELAY);
  }
  return (int)(sum / SAMPLES);
}


// ══════════════════════════════════════════════
//  SETUP
// ══════════════════════════════════════════════
void setup() {
  Serial.begin(9600);
  while (!Serial) {}

  // Ultrasonic A
  pinMode(TRIG_A, OUTPUT);
  pinMode(ECHO_A, INPUT);

  // Ultrasonic B
  pinMode(TRIG_B, OUTPUT);
  pinMode(ECHO_B, INPUT);

  // Servo
  myServo.attach(SERVO_PIN);
  myServo.write(SWEEP_START);
  delay(500);

  // Motor driver
  pinMode(PWMA, OUTPUT);
  pinMode(AIN1, OUTPUT);
  pinMode(AIN2, OUTPUT);
  pinMode(STBY, OUTPUT);
  motorStop();

  analogReference(DEFAULT);
  Serial.println(F("{\"type\":\"boot\",\"msg\":\"ready — dual sensor 360\"}"));
}


// ══════════════════════════════════════════════
//  MAIN LOOP
// ══════════════════════════════════════════════
void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    handleCommand(line);
  }

  uint32_t now = millis();
  if (now - lastPublish >= PUBLISH_EVERY && !pumpRunning) {
    lastPublish = now;
    int soil = readAverage(PIN_SOIL);
    int rain = readAverage(PIN_RAIN);

    Serial.print(F("{\"type\":\"sensor\",\"soil\":"));
    Serial.print(soil);
    Serial.print(F(",\"rain\":"));
    Serial.print(rain);
    Serial.print(F(",\"ms\":"));
    Serial.print(now);
    Serial.println(F("}"));
  }
}
