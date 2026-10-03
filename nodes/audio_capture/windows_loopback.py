"""WASAPI process loopback: capture other apps, excluding this process tree.

Unlike endpoint loopback this applies to all Windows output devices. Requires
Windows build 20348 or newer (AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK).
"""
from __future__ import annotations

import ctypes as ct
from functools import lru_cache
import os
import sys
import threading
import time

_COM_IMPORT_LOCK = threading.Lock()


def _load_comtypes():
    # soundcard may already have initialized this thread as MTA. comtypes'
    # default STA initialization on first import would otherwise fail.
    with _COM_IMPORT_LOCK:
        apartment, qualifier = ct.c_int(), ct.c_int()
        get_apartment = ct.OleDLL("ole32").CoGetApartmentType
        get_apartment.argtypes = [ct.POINTER(ct.c_int), ct.POINTER(ct.c_int)]
        try:
            get_apartment(ct.byref(apartment), ct.byref(qualifier))
            flags = 2 if apartment.value in (0, 3) else 0
        except OSError:
            flags = 0
        previous = getattr(sys, "coinit_flags", None)
        sys.coinit_flags = flags
        try:
            import comtypes
            return comtypes
        finally:
            if previous is None:
                del sys.coinit_flags
            else:
                sys.coinit_flags = previous


def is_supported():
    return sys.platform == "win32" and sys.getwindowsversion().build >= 20348


class WaveFormat(ct.Structure):
    _pack_ = 1
    _fields_ = [("tag", ct.c_ushort), ("channels", ct.c_ushort), ("rate", ct.c_uint32),
                ("bytes_per_second", ct.c_uint32), ("block_align", ct.c_ushort),
                ("bits", ct.c_ushort), ("extra_size", ct.c_ushort)]


class ActivationParams(ct.Structure):
    _fields_ = [("activation_type", ct.c_uint32), ("process_id", ct.c_uint32), ("loopback_mode", ct.c_uint32)]


class Blob(ct.Structure):
    _fields_ = [("size", ct.c_uint32), ("data", ct.POINTER(ct.c_ubyte))]


class PropVariant(ct.Structure):
    _fields_ = [("vt", ct.c_ushort), ("reserved1", ct.c_ushort), ("reserved2", ct.c_ushort),
                ("reserved3", ct.c_ushort), ("blob", Blob)]


@lru_cache(maxsize=1)
def _interfaces():
    # Keep Windows-only COM imports out of other platforms and device capture.
    comtypes = _load_comtypes()
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
    P = ct.POINTER
    uint, longlong = ct.c_uint32, ct.c_int64

    class AudioClient(IUnknown):
        _iid_ = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Initialize", (["in"], ct.c_int, "mode"), (["in"], uint, "flags"),
                      (["in"], longlong, "duration"), (["in"], longlong, "period"),
                      (["in"], P(WaveFormat), "format"), (["in"], P(GUID), "session")),
            COMMETHOD([], HRESULT, "GetBufferSize", (["out"], P(uint), "frames")),
            COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], P(longlong), "latency")),
            COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], P(uint), "padding")),
            COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], ct.c_int, "mode"),
                      (["in"], P(WaveFormat), "format"), (["out"], P(P(WaveFormat)), "closest")),
            COMMETHOD([], HRESULT, "GetMixFormat", (["out"], P(P(WaveFormat)), "format")),
            COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], P(longlong), "default"), (["out"], P(longlong), "minimum")),
            COMMETHOD([], HRESULT, "Start"),
            COMMETHOD([], HRESULT, "Stop"),
            COMMETHOD([], HRESULT, "Reset"),
            COMMETHOD([], HRESULT, "SetEventHandle", (["in"], ct.c_void_p, "handle")),
            COMMETHOD([], HRESULT, "GetService", (["in"], P(GUID), "iid"), (["out"], P(P(IUnknown)), "service")),
        ]

    class CaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetBuffer", (["out"], P(P(ct.c_ubyte)), "data"),
                      (["out"], P(uint), "frames"), (["out"], P(uint), "flags"),
                      (["out"], P(ct.c_uint64), "position"), (["out"], P(ct.c_uint64), "timestamp")),
            COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], uint, "frames")),
            COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], P(uint), "frames")),
        ]

    class ActivationOperation(IUnknown):
        _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
        _methods_ = [COMMETHOD([], HRESULT, "GetActivateResult", (["out"], P(HRESULT), "result"),
                              (["out"], P(P(IUnknown)), "interface"))]

    class Completion(IUnknown):
        _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
        _methods_ = [COMMETHOD([], HRESULT, "ActivateCompleted", (["in"], P(ActivationOperation), "operation"))]

    class AgileObject(IUnknown):
        _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
        _methods_ = []

    class Handler(comtypes.COMObject):
        _com_interfaces_ = [Completion, AgileObject]

        def __init__(self):
            self.done = threading.Event()
            self.client, self.error = None, None

        def ActivateCompleted(self, operation):
            try:
                result, interface = operation.GetActivateResult()
                if result < 0:
                    raise OSError(f"Windows audio activation failed (0x{result & 0xffffffff:08X}).")
                self.client = interface.QueryInterface(AudioClient)
            except Exception as exc:
                self.error = exc
            finally:
                self.done.set()
            return 0

    return comtypes, AudioClient, CaptureClient, ActivationOperation, Completion, Handler


class ProcessLoopbackReader:
    """Mono PCM16 reader. All methods except the activation callback run on the capture thread."""
    def __init__(self, sample_rate=16000):
        self.sample_rate = int(sample_rate)
        self._client = self._capture = None
        self._started = self._uninitialize = False
        self._pcm = bytearray()

    def __enter__(self):
        if not is_supported():
            raise RuntimeError("Exclude QubitMCP audio requires Windows build 20348 or newer.")
        comtypes, AudioClient, CaptureClient, Operation, Completion, Handler = _interfaces()
        try:
            try:
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
                self._uninitialize = True
            except OSError as exc:
                if getattr(exc, "winerror", None) != -2147417850:  # RPC_E_CHANGED_MODE: already initialized.
                    raise
            params = ActivationParams(1, os.getpid(), 1)  # EXCLUDE_TARGET_PROCESS_TREE
            variant = PropVariant(65, 0, 0, 0, Blob(ct.sizeof(params), ct.cast(ct.pointer(params), ct.POINTER(ct.c_ubyte))))
            handler = Handler()
            handler.keepalive = (params, variant)
            operation = ct.POINTER(Operation)()
            activate = ct.WinDLL("Mmdevapi").ActivateAudioInterfaceAsync
            activate.argtypes = [ct.c_wchar_p, ct.POINTER(comtypes.GUID), ct.POINTER(PropVariant),
                                 ct.POINTER(Completion), ct.POINTER(ct.POINTER(Operation))]
            activate.restype = ct.c_long
            result = activate("VAD\\Process_Loopback", ct.byref(AudioClient._iid_), ct.byref(variant),
                              handler.QueryInterface(Completion), ct.byref(operation))
            if result < 0:
                raise OSError(f"Windows audio activation failed (0x{result & 0xffffffff:08X}).")
            if not handler.done.wait(5):
                raise TimeoutError("Windows audio activation timed out.")
            if handler.error:
                raise handler.error
            self._client = handler.client
            fmt = WaveFormat(1, 1, self.sample_rate, self.sample_rate * 2, 2, 16, 0)
            self._client.Initialize(0, 0x00020000 | 0x80000000, 1_000_000, 0, ct.byref(fmt), None)
            self._capture = self._client.GetService(ct.byref(CaptureClient._iid_)).QueryInterface(CaptureClient)
            self._client.Start()
            self._started = True
            return self
        except Exception:
            self.close()
            raise

    def read(self, frames):
        wanted = int(frames) * 2
        deadline = time.monotonic() + frames / self.sample_rate
        while len(self._pcm) < wanted:
            if self._capture.GetNextPacketSize():
                data, count, flags, _position, _timestamp = self._capture.GetBuffer()
                try:
                    self._pcm.extend(bytes(count * 2) if flags & 2 else ct.string_at(data, count * 2))
                finally:
                    self._capture.ReleaseBuffer(count)
            elif time.monotonic() >= deadline:
                # Process loopback may produce no packets when every other app is
                # silent. Return timed silence so Pause/Stop and tests stay responsive.
                self._pcm.extend(bytes(wanted - len(self._pcm)))
            else:
                time.sleep(0.005)
        result = bytes(self._pcm[:wanted])
        del self._pcm[:wanted]
        return result

    def close(self):
        try:
            if self._started:
                self._client.Stop()
        finally:
            self._started = False
            self._capture = self._client = None
            if self._uninitialize:
                self._uninitialize = False
                _interfaces()[0].CoUninitialize()

    def __exit__(self, *_args):
        self.close()
