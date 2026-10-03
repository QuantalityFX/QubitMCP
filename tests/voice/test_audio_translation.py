from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6 import QtCore, QtWidgets

from nodes.audio_capture import spec as capture
from nodes.mediator_agent import spec as mediator
from nodes.voice_actor import spec as voice


APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Scene(QtCore.QObject):
    paramChanged = QtCore.Signal(str, list)
    linksChanged = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self._edges = []
        self._node_items = {}

    def node(self, name, kind, **params):
        item = SimpleNamespace(
            model=SimpleNamespace(
                name=name, kind=kind, info="",
                params=[{"name": key, "value": value} for key, value in params.items()],
            ),
            scene=lambda: self,
        )
        self._node_items[name] = item
        return item

    def connect_nodes(self, src, dst, port="", src_port=""):
        self._edges.append(SimpleNamespace(src=src, dst=dst, dst_port_name=port, src_port_name=src_port))

    def _ordered_in_edges(self, node):
        return [edge for edge in self._edges if edge.dst is node]

    _in_edges = _ordered_in_edges

    def resolve_text_value(self, node):
        return node.model.info


def mediator_controller(scene, node):
    # Exercise the real routing/dispatch methods without launching external services.
    controller = SimpleNamespace(
        _node_item=node, _ensure_scene=lambda: scene,
        _selected_prompt_profile=lambda: "translator",
        _selected_profile_prompt=lambda: "Translate into the downstream Voice Actor language.",
        _auto_enabled=lambda: True, _auto_baseline_ready=False,
        _last_voice_mode="", _last_auto_voice_token="", _last_processed_signature="",
        _running=False, _run_codex_prompt=Mock(), _set_status=Mock(),
    )
    for name in (
        "_sync_auto_baseline", "_collect_inputs", "_collect_inputs_for_profile",
        "_connected_voice_actor_item", "_connected_source_names", "_maybe_process_inputs",
    ):
        setattr(controller, name, MethodType(getattr(mediator.MediatorConsoleWidget, name), controller))
    controller._sync_auto_baseline()
    scene.paramChanged.connect(
        lambda name, _params: controller._maybe_process_inputs(name, force=False, source="auto")
    )
    return controller


class AudioTranslationTests(unittest.TestCase):
    def setUp(self):
        self.scene = Scene()
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget._mark_inactive()
            widget.deleteLater()
        APP.processEvents()

    def widget(self, node):
        widget = voice.VoiceActorWidget(node)
        self.widgets.append(widget)
        widget._refresh_source_param_options()
        return widget

    def test_duplicate_voice_wires_do_not_repeat_the_translation_input(self):
        for port in ("", "voice_input"):
            with self.subTest(port=port):
                scene = Scene()
                source = scene.node("input", "voice_actor")
                target = scene.node("translator", "mediator_agent")
                source.model.info = "A single sentence."
                scene.connect_nodes(source, target, port)
                scene.connect_nodes(source, target, port)
                controller = mediator_controller(scene, target)
                self.assertEqual(controller._collect_inputs()[2], "A single sentence.")

    def test_different_voice_sources_with_equal_text_are_preserved(self):
        target = self.scene.node("translator", "mediator_agent")
        for name in ("speaker1", "speaker2"):
            source = self.scene.node(name, "voice_actor")
            source.model.info = "Hello."
            self.scene.connect_nodes(source, target)
        controller = mediator_controller(self.scene, target)
        self.assertEqual(controller._collect_inputs()[2], "Hello.\n\nHello.")

    def test_live_audio_transcript_dispatches_once_and_response_reaches_speaker(self):
        for named_ports in (False, True):
            with self.subTest(named_ports=named_ports):
                self.scene = Scene()
                audio = self.scene.node("capture", "audio_capture")
                source = self.scene.node("listener", "voice_actor", __voice_actor_stt_language="ja")
                target = self.scene.node("translator", "medigator_agent")
                speaker = self.scene.node(
                    "speaker", "voice_actor", __voice_actor_mode="text_to_voice",
                    __voice_actor_stt_language="en",
                )
                self.scene.connect_nodes(audio, source, "audio" if named_ports else "")
                self.scene.connect_nodes(source, target, "voice_input" if named_ports else "")
                self.scene.connect_nodes(target, speaker, "text" if named_ports else "")
                listener = self.widget(source)
                output = self.widget(speaker)
                controller = mediator_controller(self.scene, target)
                config, label = listener._connected_audio_capture_config()
                self.assertIsNotNone(config)
                config["live_mode"] = True
                listener._stt_live_auto_publish = True
                listener._set_stt_state(listening=True, paused=False, stop_requested=False)
                chunk = capture.CapturedAudioChunk(pcm=b"\xe8\x03" * 1600, sample_rate=16000)
                with (
                    patch.object(capture, "capture_next_phrase", return_value=(chunk, "")),
                    patch.object(voice, "_transcribe_local_whisper", return_value=("こんにちは", "")),
                    patch.object(listener, "_resume_stt_after_send"),
                    patch.object(output, "_speak_text", return_value=True) as speak,
                ):
                    listener._stt_worker("local_whisper", "auto_respond", "ja", config, label)
                    controller._run_codex_prompt.assert_called_once()
                    prompt = controller._run_codex_prompt.call_args.args[0]
                    self.assertIn("こんにちは", prompt)
                    self.assertIn("English (en)", prompt)
                    # Response completion publishes through the scene's real Qt signal.
                    mediator._set_node_info(target, "Hello.")
                    speak.assert_called_once()
                    self.assertEqual(speak.call_args.args[0], "Hello.")
                    self.assertEqual(speak.call_args.kwargs["source"], "chatbot_auto")
                    self.scene.paramChanged.emit(target.model.name, target.model.params)
                    speak.assert_called_once()

    def test_feedback_proxy_accepts_plain_translation_without_feedback_tags(self):
        target = self.scene.node("translator", "medigator_agent")
        self.assertEqual(voice._proxy_auto_speech_text(target, "", fallback_text="Hello."), "Hello.")

    def test_same_translation_in_a_new_message_is_spoken_again(self):
        target = self.scene.node("translator", "mediator_agent")
        speaker = self.scene.node("speaker", "voice_actor", __voice_actor_mode="text_to_voice")
        self.scene.connect_nodes(target, speaker)
        output = self.widget(speaker)

        def spoken(text, *, source, session_token):
            output._readout_session_token = session_token
            output._last_tts_text = text
            return True

        # Windows can report the same clock tick for consecutive responses.
        # Identical words in distinct messages must still get distinct events.
        with patch.object(output, "_speak_text", side_effect=spoken) as speak, \
             patch.object(mediator, "datetime") as clock:
            clock.datetime.now.return_value.strftime.return_value = "20261002120000000000"
            mediator._set_node_info(target, "Hello.")
            mediator._set_node_info(target, "Hello.")
            self.assertEqual(speak.call_count, 2)

    def test_queued_repeat_in_a_new_message_is_played_after_current_speech(self):
        target = self.scene.node("translator", "mediator_agent")
        speaker = self.scene.node("speaker", "voice_actor", __voice_actor_mode="text_to_voice")
        self.scene.connect_nodes(target, speaker)
        output = self.widget(speaker)
        output._readout_session_token = "previous-message"
        output._last_tts_text = "Hello."
        output._busy = True
        with patch.object(output, "_speak_text", return_value=True) as speak:
            mediator._set_node_info(target, "Hello.")
            speak.assert_not_called()
            self.assertEqual(output._pending_chatbot_text, "Hello.")
            output._finish_tts("")
            speak.assert_called_once()

    def test_repeat_updates_within_one_speech_session_are_not_replayed(self):
        target = self.scene.node("translator", "mediator_agent")
        speaker = self.scene.node("speaker", "voice_actor", __voice_actor_mode="text_to_voice")
        self.scene.connect_nodes(target, speaker)
        output = self.widget(speaker)
        output._readout_session_token = "same-message"
        output._last_tts_text = "Hello."
        with patch.object(output, "_speak_text", return_value=True) as speak:
            mediator._publish_mediator_speech_text(target, "Hello.", session_token="same-message")
            speak.assert_not_called()


if __name__ == "__main__":
    unittest.main()
