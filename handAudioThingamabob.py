import cv2
import mediapipe as mp
import threading
import numpy as np
import sounddevice as sd
import tkinter as tk
from PIL import Image, ImageTk
from pysndfx import AudioEffectsChain
import math


class HandTracker:
    def __init__(self):
        self.hands = mp.solutions.hands.Hands(
            max_num_hands=2,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.drawing_utils = mp.solutions.drawing_utils

    def detect_hands(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.hands.process(rgb)
        return results


class AudioGeneration:
    def __init__(self):
        self.fs = 16000  # Sample rate
        self.freq = 440.0
        self.amplitude = 0.2
        self.running = False
        self.lock = threading.Lock()
        self.phase = 0.0
        self.roomSize = 0.3  # Affects reverb time
        self.phase_offset = 0

    def start_audio(self):
        if not self.running:
            self.running = True
            threading.Thread(target=self.audioGen, daemon=True).start()

    def stop_audio(self):
        with self.lock:
            self.running = False

    def set_frequency(self, freq):
        with self.lock:
            self.freq = freq

    def set_amplitude(self, amplitude):
        with self.lock:
            self.amplitude = amplitude

    def set_room_size(self, roomSize):
        with self.lock:
            self.roomSize = roomSize

    def set_parameters(self, freq, amplitude, roomSize):
        with self.lock:
            alpha = 0.2  # Smoothing factor
            self.freq = self.freq * (1 - alpha) + freq * alpha
            self.amplitude = self.amplitude * (1 - alpha) + amplitude * alpha
            self.roomSize = self.roomSize * (1 - alpha) + roomSize * alpha

    def apply_reverb(self, signal, reverb_time, decay_factor=0.6, num_echoes=5):
        """
        Apply basic reverb using delayed and decayed signal copies.
        """
        reverb_signal = np.copy(signal)
        delay_samples = int((reverb_time / num_echoes) * self.fs)

        for i in range(1, num_echoes + 1):
            decay = decay_factor ** i
            echo = np.zeros_like(signal)
            if delay_samples * i < len(signal):
                echo[delay_samples * i:] = signal[:-delay_samples * i] * decay
                reverb_signal += echo

        return np.clip(reverb_signal, -1.0, 1.0)

    def audioGen(self):
        def callback(outdata, frames, time, status):
            with self.lock:
                if not self.running:
                    raise sd.CallbackStop()

                t = (np.arange(frames) + self.phase_offset) / self.fs
                samples = self.amplitude * np.sin(2 * np.pi * self.freq * t).astype(np.float32)

                # Apply reverb using roomSize as the reverb time
                samples = self.apply_reverb(samples, self.roomSize)

                outdata[:, 0] = samples
                self.phase_offset += frames
                self.phase_offset %= self.fs

        with sd.OutputStream(channels=1, callback=callback, samplerate=self.fs, dtype='float32', blocksize=1024):
            while True:
                with self.lock:
                    if not self.running:
                        break
                sd.sleep(100)





class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Hand Tracker with Audio")

        self.cap = cv2.VideoCapture(0)
        self.hand_tracker = HandTracker()
        self.audio_gen = AudioGeneration()

        self.canvas = tk.Canvas(root, width=640, height=480)
        self.canvas.pack()

        self.frame_count = 0
        self.freq_buffer = []
        self.amp_buffer = []
        self.roomSize_buffer = []
        self.smoothing_window = 5
        self.update_interval = 5  # update audio every N frames

        self.update_video()

    def update_video(self):
        ret, frame = self.cap.read()

        if ret:
            frame = cv2.flip(frame, 1)
            results = self.hand_tracker.detect_hands(frame)

            saw_left = False
            saw_right = False

            if results.multi_hand_landmarks:
                self.audio_gen.start_audio()
                for idx, hand_landmarks in enumerate(results.multi_hand_landmarks):
                    handedness = results.multi_handedness[idx].classification[0].label

                    self.hand_tracker.drawing_utils.draw_landmarks(
                        frame, hand_landmarks, mp.solutions.hands.HAND_CONNECTIONS
                    )

                    # Palm position (landmark 0)
                    if handedness == "Left":
                        saw_left = True
                        Lpalm_pos = hand_landmarks.landmark[0]

                        # Map Lpalm_x to frequency
                        Lpalmpos_x = 1.0 - Lpalm_pos.x
                        freq = 220 + (880 - 220) * Lpalmpos_x

                        # Map palm_y to amplitude
                        Lpalmpos_y = 1.0 - Lpalm_pos.y
                        amplitude = 0.1 + 0.4 * Lpalmpos_y

                        # Store recent values for smoothing
                        self.freq_buffer.append(freq)
                        self.amp_buffer.append(amplitude)
                        if len(self.freq_buffer) > self.smoothing_window:
                            self.freq_buffer.pop(0)
                            self.amp_buffer.pop(0)
                    if handedness == "Right":
                        saw_right = True
                            # Mapping distance between thumb and index for reverb
                        thumbTip_pos = hand_landmarks.landmark[4]
                        indexTip_pos = hand_landmarks.landmark[8]

                        thumbTip_posX = 1.0 - thumbTip_pos.x
                        thumbTip_posY = 1.0 - thumbTip_pos.y
                        indexTip_posX = 1.0 - indexTip_pos.x
                        indexTip_posY = 1.0 - indexTip_pos.y

                        tipDistance_reverb = math.hypot(
                            thumbTip_posX - indexTip_posX, thumbTip_posY - indexTip_posY
                        )

                        # Normalize and map distance to a room size (e.g., 0.1 to 1.0)
                        # Assuming hand moves ~0.05 to 0.4 range in distance
                        min_dist = 0.03
                        max_dist = 0.4
                        normalized_dist = (tipDistance_reverb - min_dist) / (max_dist - min_dist)
                        normalized_dist = min(max(normalized_dist, 0.0), 1.0)

                        # Map to roomSize: 0.1 to 1.0 (reverb time)
                        room_size = 0.1 + normalized_dist * 0.9
                        self.roomSize_buffer.append(room_size)
                        if len(self.roomSize_buffer) > self.smoothing_window:
                            self.roomSize_buffer.pop(0)
                    self.frame_count += 1
                    if self.frame_count % self.update_interval == 0 and (saw_left or saw_right):
                        avg_freq = sum(self.freq_buffer) / len(
                            self.freq_buffer) if self.freq_buffer else self.audio_gen.freq
                        avg_amp = sum(self.amp_buffer) / len(
                            self.amp_buffer) if self.amp_buffer else self.audio_gen.amplitude
                        avg_roomSize = sum(self.roomSize_buffer) / len(
                            self.roomSize_buffer) if self.roomSize_buffer else self.audio_gen.roomSize

                        self.audio_gen.set_parameters(avg_freq, avg_amp, avg_roomSize)




            else:
                self.audio_gen.stop_audio()

            img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = ImageTk.PhotoImage(Image.fromarray(img))
            self.canvas.create_image(0, 0, anchor=tk.NW, image=img)
            self.canvas.imgtk = img

        self.root.after(10, self.update_video)

    def on_close(self):
        self.audio_gen.stop_audio()
        self.cap.release()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()

## To Do:
# Add a slider to adjust starting frequence
# buttons to adjust soundwave
# reverb control thumb and index tips.
# can feed outstream from sd into the system again to apply effects?
#

