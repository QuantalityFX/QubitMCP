from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import audioop
import ctypes as ct
import math
import unittest
from unittest.mock import Mock, patch

from PySide6 import QtWidgets

from nodes.audio_capture import spec as capture, stream, windows_loopback
from nodes.voice_actor import spec as voice
from nodes.voice_actor.stt_audio import prepare_speech_audio
from tests.voice.test_audio_translation import Scene

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FeedbackCaptureTests(unittest.TestCase):
    def test_output_capture_excludes_app_by_default_on_supported_windows(self):
        item = Scene().node("Earbud capture", "audio_capture", source_mode="output_device", device_id="earbuds")
        with patch.object(capture, "_DEFAULT_EXCLUDE_APP_AUDIO", True), \
             patch.dict(capture._PARAM_DEFAULTS, exclude_app_audio="1"):
            config = capture.config_from_node_item(item)
        self.assertTrue(config["exclude_app_audio"])
        backend = Mock()
        backend.read.return_value = b"\0\0" * 800
        manager = Mock(__enter__=Mock(return_value=backend), __exit__=Mock(return_value=False))
        with patch.object(windows_loopback, "ProcessLoopbackReader", return_value=manager) as excluded, \
             patch.object(capture, "_soundcard_speaker_for_config") as mixed:
            with stream.open_audio_reader(config) as (read, rate, label):
                self.assertEqual(len(read()), 1600)
                self.assertEqual(rate, 16000)
                self.assertIn("QubitMCP excluded", label)
            excluded.assert_called_once_with(16000)
            mixed.assert_not_called()

    def test_exclusion_failure_never_falls_back_to_feedback_prone_device_mix(self):
        config = {"source_mode": "output_device", "exclude_app_audio": True}
        with patch.object(windows_loopback, "ProcessLoopbackReader", side_effect=OSError("activation failed")), \
             patch.object(capture, "_capture_next_phrase_soundcard") as mixed:
            chunk, error = capture.capture_next_phrase(config)
            self.assertIsNone(chunk)
            self.assertIn("activation failed", error)
            mixed.assert_not_called()

    def test_microphone_capture_does_not_apply_output_exclusion(self):
        config = {"source_mode": "input_device", "exclude_app_audio": True}
        self.assertFalse(capture._exclude_app_audio_enabled(config))
        with patch.object(capture, "_capture_next_phrase_pyaudio", return_value=(None, "")) as microphone, \
             patch.object(windows_loopback, "ProcessLoopbackReader") as excluded:
            capture.capture_next_phrase(config)
            microphone.assert_called_once()
            excluded.assert_not_called()

    def test_pcm_packets_are_read_once_and_silent_packets_preserve_duration(self):
        native = windows_loopback.ProcessLoopbackReader(8000)
        first = (ct.c_ubyte * 8).from_buffer_copy(b"\x10\0\x20\0\x30\0\x40\0")
        native._capture = Mock()
        native._capture.GetNextPacketSize.side_effect = [4, 4]
        native._capture.GetBuffer.side_effect = [(ct.cast(first, ct.POINTER(ct.c_ubyte)), 4, 0, 0, 0),
                                                (None, 4, 2, 0, 0)]
        self.assertEqual(native.read(2), b"\x10\0\x20\0")
        self.assertEqual(native.read(6), b"\x30\0\x40\0" + bytes(8))
        self.assertEqual(native._capture.ReleaseBuffer.call_count, 2)

    def test_no_packets_returns_timed_silence_so_stop_remains_responsive(self):
        native = windows_loopback.ProcessLoopbackReader(8000)
        native._capture = Mock()
        native._capture.GetNextPacketSize.return_value = 0
        with patch.object(windows_loopback.time, "monotonic", side_effect=[1.0, 1.1]):
            self.assertEqual(native.read(400), bytes(800))
        native._capture.GetBuffer.assert_not_called()


class RecognitionAudioTests(unittest.TestCase):
    def test_minus_35_db_audio_is_boosted_without_changing_the_original_sample(self):
        amplitude = round(32768 * 10 ** (-35 / 20))
        pcm = amplitude.to_bytes(2, "little") * 16000
        original = capture.CapturedAudioChunk(pcm, 16000)
        prepared = prepare_speech_audio(original)
        db = 20 * math.log10(audioop.rms(prepared.pcm, 2) / 32768)
        self.assertAlmostEqual(db, -20, delta=0.05)
        self.assertEqual(original.pcm, pcm)
        self.assertEqual(len(prepared.pcm), len(pcm))

    def test_gain_preserves_peak_headroom_and_does_not_amplify_silence(self):
        pcm = b"\x20\0" * 8000 + int(30000).to_bytes(2, "little")
        prepared = prepare_speech_audio(capture.CapturedAudioChunk(pcm, 8000))
        self.assertLessEqual(audioop.max(prepared.pcm, 2), 32768 * 0.95)
        self.assertIsNone(prepare_speech_audio(capture.CapturedAudioChunk(bytes(16000), 8000)))
        low = prepare_speech_audio(capture.CapturedAudioChunk(b"\x10\0" * 8000, 8000))
        self.assertLessEqual(audioop.max(low.pcm, 2), 16 * 12)


class FeedbackUITests(unittest.TestCase):
    def setUp(self):
        self.scene, self.widgets = Scene(), []

    def tearDown(self):
        for widget in self.widgets:
            if isinstance(widget, voice.VoiceActorWidget):
                widget._mark_inactive()
            widget.deleteLater()
        APP.processEvents()

    def listener(self):
        node = self.scene.node("Listener", "voice_actor", __voice_actor_send_mode="audio_stream")
        widget = voice.VoiceActorWidget(node)
        self.widgets.append(widget)
        widget._stt_streaming = widget._stt_live_auto_publish = True
        widget._set_stt_state(listening=True)
        return widget

    def test_checkbox_explains_all_outputs_and_preserves_selected_earbuds(self):
        node = self.scene.node("Capture", "audio_capture", device_id="earbuds", exclude_app_audio="1")
        with patch.object(capture, "available_targets", return_value=[{"id": "earbuds", "name": "Earbuds"}]), \
             patch.object(capture, "_process_loopback_supported", return_value=True):
            widget = capture.AudioCaptureWidget(node)
            self.widgets.append(widget)
            self.assertFalse(widget._device_combo.isEnabled())
            self.assertIn("all outputs", widget._exclude_app_check.text())
            widget._exclude_app_check.setChecked(False)
            self.assertTrue(widget._device_combo.isEnabled())
            self.assertFalse(widget._config()["exclude_app_audio"])
            self.assertEqual(widget._config()["device_id"], "earbuds")
            widget._mode_combo.setCurrentIndex(widget._mode_combo.findData("input_device"))
            self.assertFalse(widget._exclude_app_check.isEnabled())

    def test_google_retries_unrecognized_audio_with_next_chunk_and_sends_once(self):
        widget = self.listener()
        chunk = capture.CapturedAudioChunk(b"\xe8\x03" * 8000, 8000)
        recognizer = Mock()
        recognizer.recognize_google.side_effect = [voice.sr.UnknownValueError(), "A complete sentence."]
        with patch.object(stream, "iter_audio_chunks", return_value=iter([chunk, chunk])), \
             patch.object(voice.sr, "Recognizer", return_value=recognizer), \
             patch.object(voice, "_publish_voice_command") as publish:
            widget._stt_worker("google", "audio_stream", "en", {})
        self.assertEqual([len(c.args[0].frame_data) for c in recognizer.recognize_google.call_args_list], [16000, 32000])
        publish.assert_called_once_with(widget._node_item, "A complete sentence.")
        self.assertNotIn("could not", widget._status.text())

    def test_whisper_retries_empty_transcript_with_context_and_ignores_silence(self):
        widget = self.listener()
        silence = capture.CapturedAudioChunk(bytes(16000), 8000)
        chunk = capture.CapturedAudioChunk(b"\xe8\x03" * 8000, 8000)
        with patch.object(stream, "iter_audio_chunks", return_value=iter([silence, chunk, chunk])), \
             patch.object(voice, "_transcribe_local_whisper", side_effect=[("", "Speech detected but transcript was empty."),
                                                                         ("A complete sentence.", "")]) as transcribe:
            widget._stt_worker("local_whisper", "audio_stream", "en", {})
        self.assertEqual(transcribe.call_count, 2)
        self.assertEqual([len(c.args[0]) for c in transcribe.call_args_list], [16044, 32044])
        self.assertEqual(widget._transcript.toPlainText(), "A complete sentence.")

    def test_unrecognized_audio_context_is_bounded(self):
        widget = self.listener()
        chunk = capture.CapturedAudioChunk(b"\xe8\x03" * 40000, 8000)
        with patch.object(stream, "iter_audio_chunks", return_value=iter([chunk] * 4)), \
             patch.object(voice, "_transcribe_local_whisper", return_value=("", "Speech detected but transcript was empty.")) as transcribe:
            widget._stt_worker("local_whisper", "audio_stream", "en", {})
        self.assertEqual([len(c.args[0]) - 44 for c in transcribe.call_args_list], [80000, 160000, 240000, 80000])
        self.assertIn("No speech transcribed", widget._status.text())


if __name__ == "__main__":
    unittest.main()
