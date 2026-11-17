import unittest
from queue import Empty

from voice.wakeword_audio import (
    SoundDeviceWakeWordAudioSource,
    WakeWordAudioError,
)


class FakeRawInputStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def emit(self, data, status=None):
        self.callback(data, 512, None, status)

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class FakeSoundDevice:
    def __init__(self):
        self.streams = []

    def RawInputStream(self, **kwargs):
        stream = FakeRawInputStream(**kwargs)
        self.streams.append(stream)
        return stream


class WakeWordAudioSourceTests(unittest.TestCase):
    def test_construction_does_not_open_device(self):
        sd = FakeSoundDevice()
        source = SoundDeviceWakeWordAudioSource(sounddevice_module=sd)
        self.assertEqual(sd.streams, [])

    def test_start_uses_fixed_pcm16_format_and_reads_local_frame(self):
        sd = FakeSoundDevice()
        source = SoundDeviceWakeWordAudioSource(sounddevice_module=sd)
        source.start()
        stream = sd.streams[0]
        self.assertTrue(stream.started)
        self.assertEqual(stream.kwargs["samplerate"], 16000)
        self.assertEqual(stream.kwargs["channels"], 1)
        self.assertEqual(stream.kwargs["dtype"], "int16")
        self.assertEqual(stream.kwargs["blocksize"], 512)
        frame = bytes([1, 0]) * 512
        stream.emit(frame)
        self.assertEqual(source.read(), frame)
        source.close()
        self.assertTrue(stream.stopped)
        self.assertTrue(stream.closed)

    def test_queue_retains_only_latest_frame(self):
        sd = FakeSoundDevice()
        source = SoundDeviceWakeWordAudioSource(sounddevice_module=sd)
        source.start()
        stream = sd.streams[0]
        first = bytes([1, 0]) * 512
        latest = bytes([2, 0]) * 512
        stream.emit(first)
        stream.emit(latest)
        self.assertEqual(source.read(), latest)
        source.close()

    def test_stop_interrupts_read_and_discards_pending_audio(self):
        sd = FakeSoundDevice()
        source = SoundDeviceWakeWordAudioSource(sounddevice_module=sd)
        source.start()
        stream = sd.streams[0]
        stream.emit(bytes([1, 0]) * 512)
        source.stop()
        self.assertIsNone(source.read())
        self.assertTrue(stream.stopped)
        source.close()
        self.assertTrue(stream.closed)

    def test_malformed_callback_frames_are_discarded(self):
        sd = FakeSoundDevice()
        source = SoundDeviceWakeWordAudioSource(sounddevice_module=sd)
        source.start()
        stream = sd.streams[0]
        stream.emit(b"short")
        with self.assertRaises(Empty):
            # No malformed data should be made available to the detector.
            source._frames.get(timeout=0.01)
        source.close()

    def test_driver_status_frames_are_discarded(self):
        sd = FakeSoundDevice()
        source = SoundDeviceWakeWordAudioSource(sounddevice_module=sd)
        source.start()
        stream = sd.streams[0]
        stream.emit(bytes([1, 0]) * 512, status="input overflow")
        with self.assertRaises(Empty):
            source._frames.get(timeout=0.01)
        source.close()

    def test_start_after_close_is_rejected(self):
        source = SoundDeviceWakeWordAudioSource(sounddevice_module=FakeSoundDevice())
        source.close()
        with self.assertRaises(WakeWordAudioError):
            source.start()

    def test_repeated_start_is_rejected(self):
        sd = FakeSoundDevice()
        source = SoundDeviceWakeWordAudioSource(sounddevice_module=sd)
        source.start()
        with self.assertRaises(WakeWordAudioError):
            source.start()
        source.close()


if __name__ == "__main__":
    unittest.main()
