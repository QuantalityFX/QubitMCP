from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from contextlib import contextmanager
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from PySide6 import QtCore, QtTest, QtWidgets

from nodes.audio_capture import spec as capture
from nodes.audio_capture import stream
from nodes.voice_actor import spec as voice
from tests.voice.test_audio_translation import Scene


APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@contextmanager
def reader(blocks, *, rate=8000, before_read=lambda index: None):
    iterator = iter(blocks)
    index = 0

    def read():
        nonlocal index
        before_read(index)
        index += 1
        return next(iterator)
    yield read, rate, "Test source"


class CaptureStreamTests(unittest.TestCase):
    def test_input_device_is_opened_once_and_released(self):
        pa, device = Mock(), Mock()
        pa.open.return_value = device
        device.read.return_value = b"\xe8\x03" * 400
        backend = Mock(paInt16=8)
        backend.PyAudio.return_value = pa
        config = {"source_mode": "input_device"}
        with patch.object(capture, "pyaudio", backend), \
             patch.object(capture, "_pyaudio_input_for_config", return_value={"index": 2, "sample_rate": 8000}):
            with stream.open_audio_reader(config) as (read, rate, _label):
                self.assertEqual(rate, 8000)
                read()
                read()
        pa.open.assert_called_once()
        self.assertEqual(device.read.call_count, 2)
        device.read.assert_called_with(400, exception_on_overflow=True)
        device.stop_stream.assert_called_once()
        device.close.assert_called_once()
        pa.terminate.assert_called_once()

    def test_output_device_keeps_loopback_recorder_open(self):
        recorder = Mock()
        recorder.record.return_value = capture.np.ones((400, 2), dtype=capture.np.float32) * 0.1
        loopback = Mock()
        loopback.recorder.return_value.__enter__ = Mock(return_value=recorder)
        loopback.recorder.return_value.__exit__ = Mock(return_value=False)
        with patch.object(capture, "sc", Mock()), \
             patch.object(capture, "_soundcard_speaker_for_config", return_value=Mock(name="speaker")), \
             patch.object(capture, "_soundcard_loopback_for_speaker", return_value=loopback):
            with stream.open_audio_reader({"source_mode": "output_device", "sample_rate": 8000, "exclude_app_audio": False}) as (read, _, _):
                self.assertEqual(len(read()), 800)
                self.assertEqual(len(read()), 800)
        loopback.recorder.assert_called_once_with(samplerate=8000, blocksize=400)
        loopback.recorder.return_value.__exit__.assert_called_once()

    def test_one_recorder_preserves_chunk_boundaries_and_stop_tail(self):
        blocks = [int(i + 1000).to_bytes(2, "little") * 400 for i in range(47)]
        count = 0

        def advance(_index):
            nonlocal count
            count += 1

        with patch.object(stream, "open_audio_reader", return_value=reader(blocks, before_read=advance)) as opened:
            chunks = list(stream.iter_audio_chunks({"threshold": 0.001, "phrase_ms": 1000},
                                                  should_stop=lambda: count == len(blocks)))
        opened.assert_called_once()
        self.assertEqual([len(c.pcm) for c in chunks], [16000, 16000, 5600])
        self.assertEqual(b"".join(c.pcm for c in chunks), b"".join(blocks))

    def test_pause_preserves_prior_speech_and_excludes_paused_audio(self):
        blocks = [int(i + 1000).to_bytes(2, "little") * 400 for i in range(7)]
        index = -1

        def advance(value):
            nonlocal index
            index = value

        with patch.object(stream, "open_audio_reader", return_value=reader(blocks, before_read=advance)):
            chunks = list(stream.iter_audio_chunks({"threshold": 0.001}, should_stop=lambda: index == 6,
                                                  is_paused=lambda: 2 <= index <= 3))
        self.assertEqual([c.pcm for c in chunks], [b"".join(blocks[:2]), b"".join(blocks[4:])])

    def test_quiet_leading_audio_is_kept_before_speech(self):
        blocks = [b"\x01\x00" * 400] * 5 + [b"\xe8\x03" * 400]
        count = 0

        def advance(_index):
            nonlocal count
            count += 1

        with patch.object(stream, "open_audio_reader", return_value=reader(blocks, before_read=advance)):
            chunks = list(stream.iter_audio_chunks({}, should_stop=lambda: count == 6))
        self.assertEqual(chunks[0].pcm, b"".join(blocks))

    def test_silent_level_test_finishes_at_fixed_duration(self):
        levels = []
        with patch.object(stream, "open_audio_reader", return_value=reader([b"\0\0" * 400] * 6)):
            sample = stream.record_sample({"live_mode": True}, seconds=0.3, on_level=levels.append)
        self.assertEqual(len(sample.pcm), 4800)
        self.assertEqual(levels, [0.0] * 6)

    def test_queue_records_ahead_and_drains_after_stop(self):
        recorded = threading.Event()
        chunks = [capture.CapturedAudioChunk(bytes([index, 0]) * 8000, 8000) for index in range(3)]

        def source(*_args, **_kwargs):
            yield from chunks
            recorded.set()

        with patch.object(stream, "iter_audio_chunks", side_effect=source):
            with stream.BufferedAudioStream({}, should_stop=lambda: False) as buffered:
                folder = Path(buffered._spool.name)
                self.assertTrue(recorded.wait(2), "Recording must finish before decoding consumes the first chunk")
                self.assertEqual(buffered.pending_seconds, 3.0)
                self.assertEqual(len(list(folder.glob("*.pcm"))), 3)
                self.assertEqual([buffered.get().pcm for _ in chunks], [c.pcm for c in chunks])
                self.assertEqual(buffered.pending_seconds, 0.0)
                self.assertEqual(list(folder.iterdir()), [])
        self.assertFalse(folder.exists())

    def test_capture_failure_keeps_already_queued_speech(self):
        def source(*_args, **_kwargs):
            yield capture.CapturedAudioChunk(b"\x01\0" * 800, 8000)
            raise OSError("device disconnected")

        with patch.object(stream, "iter_audio_chunks", side_effect=source):
            with stream.BufferedAudioStream({}, should_stop=lambda: False) as buffered:
                self.assertTrue(buffered._done.wait(2))
                self.assertIsNotNone(buffered.get())
                self.assertTrue(buffered.finished)
                self.assertIn("device disconnected", buffered.error)


class AudioStreamUITests(unittest.TestCase):
    def setUp(self):
        self.scene = Scene()
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            if isinstance(widget, voice.VoiceActorWidget):
                widget._mark_inactive()
            widget.deleteLater()
        APP.processEvents()

    def listener(self):
        source = self.scene.node("capture", "audio_capture")
        item = self.scene.node("listener", "voice_actor", __voice_actor_send_mode="audio_stream")
        self.scene.connect_nodes(source, item, "audio")
        widget = voice.VoiceActorWidget(item)
        self.widgets.append(widget)
        return widget

    def test_option_persists_and_does_not_change_auto_respond(self):
        widget = self.listener()
        self.assertEqual(widget._send_mode_combo.currentText(), "Audio Stream")
        widget._send_mode_combo.setCurrentIndex(widget._send_mode_combo.findData(voice.STT_SEND_MODE_AUTO_RESPOND))
        self.assertEqual(voice._param_value(widget._node_item.model, voice.VOICE_ACTOR_SEND_MODE_KEY), "auto_respond")
        widget._send_mode_combo.setCurrentIndex(widget._send_mode_combo.findText("Audio Stream"))
        self.assertEqual(voice._param_value(widget._node_item.model, voice.VOICE_ACTOR_SEND_MODE_KEY), "audio_stream")

    def test_stream_keeps_recording_during_decode_and_publishes_all_chunks_once(self):
        widget = self.listener()
        widget._stt_streaming = widget._stt_live_auto_publish = True
        widget._set_stt_state(listening=True)
        widget._set_busy(True, "listening")
        recorded = threading.Event()
        chunks = [capture.CapturedAudioChunk(int(800 + index).to_bytes(2, "little") * 800, 8000) for index in range(3)]
        text = iter(["First sentence.", "stop speaking", "Final sentence."])
        published = []
        self.scene.paramChanged.connect(lambda name, params: published.append(
            (widget._node_item.model.info, voice._param_value(widget._node_item.model, voice.VOICE_ACTOR_SEND_TOKEN_KEY))
        ) if name == "listener" else None)

        def source(*_args, **_kwargs):
            yield from chunks
            widget._set_stt_state(stop_requested=True)
            recorded.set()

        def transcribe(*_args):
            self.assertTrue(recorded.wait(2), "Capture must proceed while transcription waits")
            return next(text), ""

        with patch.object(stream, "iter_audio_chunks", side_effect=source), \
             patch.object(voice, "_transcribe_local_whisper", side_effect=transcribe), \
             patch.object(widget, "_resume_stt_after_send") as restart:
            widget._stt_worker("local_whisper", "audio_stream", "en", {"live_mode": False})
            APP.processEvents()
            restart.assert_not_called()
        self.assertEqual([p[0] for p in published], ["First sentence.", "stop speaking", "Final sentence."])
        self.assertEqual(len({p[1] for p in published}), 3)
        self.assertEqual(widget._transcript.toPlainText(), "First sentence. stop speaking Final sentence.")
        self.assertEqual(widget._node_item.model.info, "Final sentence.")
        self.assertFalse(widget._busy)
        self.assertFalse(widget._stt_state()[0])
        self.assertEqual(widget._transcript_undo_stack, [])

    def test_stream_is_not_stopped_by_another_actors_turn_taking(self):
        widget = self.listener()
        widget._stt_streaming = True
        widget._set_stt_state(listening=True)
        self.assertFalse(widget._pause_listening_for_turn_taking())
        self.assertEqual(widget._stt_state(), (True, False, False))

    def test_removed_widget_stops_stream_and_ignores_queued_transcripts(self):
        widget = self.listener()
        widget._stt_streaming = widget._stt_live_auto_publish = True
        widget._mark_inactive()
        with patch.object(voice, "_publish_voice_command") as publish:
            widget._on_stt_chunk("Late transcript")
            widget._finish_stt("", "__stream_stopped__")
            publish.assert_not_called()
        self.assertTrue(widget._stt_abort_event.is_set())
        self.assertEqual(widget._transcript.toPlainText(), "")

    def test_start_routes_selected_mode_to_stream_with_live_disabled(self):
        widget = self.listener()
        with patch.object(widget, "_connected_audio_capture_config", return_value=({"live_mode": False}, "Test")), \
             patch.object(voice, "WhisperModel", Mock()), patch.object(voice, "sr", Mock()), \
             patch.object(voice.threading, "Thread") as worker:
            widget._start_stt()
            self.assertTrue(widget._stt_streaming)
            self.assertTrue(widget._stt_live_auto_publish)
            self.assertEqual(worker.call_args.kwargs["args"][1], "audio_stream")
            worker.return_value.start.assert_called_once()

    def test_missing_stream_source_keeps_existing_transcript(self):
        widget = self.listener()
        widget._set_transcript("Keep this text.", publish=False)
        with patch.object(widget, "_connected_audio_capture_config", return_value=(None, "")), \
             patch.object(voice, "WhisperModel", Mock()), patch.object(voice, "sr", Mock()), \
             patch.object(voice.threading, "Thread") as worker:
            widget._start_stt()
            worker.assert_not_called()
        self.assertEqual(widget._transcript.toPlainText(), "Keep this text.")
        self.assertIn("Connect an Audio Capture", widget._status.text())

    def test_google_stream_retains_later_speech_after_one_recognition_error(self):
        widget = self.listener()
        widget._stt_streaming = widget._stt_live_auto_publish = True
        widget._set_stt_state(listening=True)
        chunks = [capture.CapturedAudioChunk(b"\x20\0" * 800, 8000)] * 2
        recognizer = Mock()
        recognizer.recognize_google.side_effect = [RuntimeError("recognizer unavailable"), "Recovered sentence."]
        with patch.object(stream, "iter_audio_chunks", return_value=iter(chunks)), \
             patch.object(voice.sr, "Recognizer", return_value=recognizer):
            widget._stt_worker("google", "audio_stream", "ja", {})
        self.assertEqual(recognizer.operation_timeout, 15)
        self.assertEqual(recognizer.recognize_google.call_args.kwargs["language"], "ja-JP")
        self.assertEqual(widget._transcript.toPlainText(), "Recovered sentence.")
        self.assertIn("1 segment(s) could not be transcribed", widget._status.text())

    def test_test_and_listen_shows_quiet_audio_and_replays_exact_sample(self):
        item = self.scene.node("capture", "audio_capture")
        with patch.object(capture, "available_targets", return_value=[]):
            widget = capture.AudioCaptureWidget(item)
        self.widgets.append(widget)
        sample = capture.CapturedAudioChunk(b"\x20\0" * 800, 8000)
        with patch.object(widget, "_play_test_sample") as play:
            widget._on_sample_level(0.01)
            self.assertGreater(widget._level.value(), 20)
            self.assertEqual(widget._level.format(), "-40 dBFS")
            widget._on_sample_done(sample, "")
            play.assert_called_once_with(sample.wav_bytes())
            self.assertIn("-40 dBFS", widget._test_summary)

    def test_test_button_records_fixed_sample_before_playback(self):
        item = self.scene.node("capture", "audio_capture")
        with patch.object(capture, "available_targets", return_value=[]):
            widget = capture.AudioCaptureWidget(item)
        self.widgets.append(widget)
        sample = capture.CapturedAudioChunk(b"\0\0" * 800, 8000)
        with patch.object(stream, "record_sample", return_value=sample) as record, \
             patch.object(widget, "_play_test_sample") as play:
            widget._test_level()
            for _ in range(100):
                if play.called:
                    break
                QtTest.QTest.qWait(10)
            record.assert_called_once()
            self.assertEqual(record.call_args.kwargs["seconds"], 3.0)
            play.assert_called_once_with(sample.wav_bytes())
            self.assertIn("No signal detected", widget._test_summary)
            widget._finish_test()
            self.assertTrue(widget._test_btn.isEnabled())

    def test_playback_uses_wav_file_and_cleans_up_on_stop(self):
        from PySide6 import QtMultimedia
        item = self.scene.node("capture", "audio_capture")
        with patch.object(capture, "available_targets", return_value=[]):
            widget = capture.AudioCaptureWidget(item)
        self.widgets.append(widget)
        data = capture.CapturedAudioChunk(b"\0\0" * 800, 8000).wav_bytes()
        with patch.object(QtMultimedia, "QSoundEffect") as factory:
            player = factory.return_value
            widget._play_test_sample(data)
            path = Path(player.setSource.call_args.args[0].toLocalFile())
            self.assertEqual(path.read_bytes(), data)
            player.play.assert_called_once()
            widget._finish_test()
            player.stop.assert_called_once()
            self.assertFalse(path.exists())

    def test_capture_test_error_reenables_controls(self):
        item = self.scene.node("capture", "audio_capture")
        with patch.object(capture, "available_targets", return_value=[]):
            widget = capture.AudioCaptureWidget(item)
        self.widgets.append(widget)
        with patch.object(stream, "record_sample", side_effect=RuntimeError("device unavailable")):
            widget._test_level()
            for _ in range(100):
                if not widget._testing:
                    break
                QtTest.QTest.qWait(10)
        self.assertFalse(widget._testing)
        self.assertTrue(widget._test_btn.isEnabled())
        self.assertEqual(widget._device_combo.isEnabled(), not widget._exclude_app_check.isChecked())
        self.assertIn("device unavailable", widget._status.text())

    def test_new_controls_fit_node_borders(self):
        from echograph.model import GraphNode
        from echograph.ui.node_item import NodeItem
        with patch.object(capture, "available_targets", return_value=[]):
            for kind in ("audio_capture", "voice_actor"):
                with self.subTest(kind=kind):
                    node = NodeItem(GraphNode(name=kind, kind=kind, params=[]))
                    for proxy in node._plugin_proxies:
                        widget = proxy.widget()
                        self.widgets.append(widget)
                        widget._set_status("Recording a test sample from the selected device. Speak or play audio so you can check its level.")
                        widget.ensurePolished()
                        widget.layout().activate()
                        self.assertGreaterEqual(node.height, proxy.pos().y() + proxy.size().height() + 5)
                        self.assertGreaterEqual(proxy.size().height(), widget.minimumSizeHint().height())
                        self.assertLessEqual(widget._status.geometry().bottom(), widget.height())
                        if kind == "audio_capture":
                            self.assertLessEqual(widget._test_btn.geometry().right(), widget.width())


if __name__ == "__main__":
    unittest.main()
