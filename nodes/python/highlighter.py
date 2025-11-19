# nodes/python/highlighter.py
from __future__ import annotations

try:
    from PySide6 import QtCore, QtGui
except Exception:  # pragma: no cover - fallback for hosts pinned to Qt5
    from PySide2 import QtCore, QtGui  # type: ignore

__all__ = ["PythonSyntaxHighlighter"]


class PythonSyntaxHighlighter(QtGui.QSyntaxHighlighter):
    """
    Lightweight Python syntax highlighter that keeps the inline editors readable.
    Works with both PySide6 and PySide2 thanks to QtCore/QRegularExpression.
    """

    _KEYWORDS = (
        "and",
        "as",
        "assert",
        "async",
        "await",
        "break",
        "class",
        "continue",
        "def",
        "del",
        "elif",
        "else",
        "except",
        "False",
        "finally",
        "for",
        "from",
        "global",
        "if",
        "import",
        "in",
        "is",
        "lambda",
        "None",
        "nonlocal",
        "not",
        "or",
        "pass",
        "raise",
        "return",
        "True",
        "try",
        "while",
        "with",
        "yield",
    )

    _BUILTINS = (
        "abs",
        "all",
        "any",
        "bool",
        "bytes",
        "dict",
        "enumerate",
        "float",
        "int",
        "len",
        "list",
        "max",
        "min",
        "object",
        "print",
        "range",
        "set",
        "str",
        "sum",
        "tuple",
        "type",
        "Path",  # common pathlib alias – highlight like other builtins for readability
        "self",
        "cls",
    )

    def __init__(self, document: QtGui.QTextDocument):
        super().__init__(document)
        self._rules: list[tuple[QtCore.QRegularExpression, QtGui.QTextCharFormat]] = []

        keyword_fmt = self._build_format("#7dd3fc", bold=True)
        builtin_fmt = self._build_format("#c084fc")
        number_fmt = self._build_format("#f9a8d4")
        decorator_fmt = self._build_format("#f97316")
        variable_fmt = self._build_format("#f472b6")
        comment_fmt = self._build_format("#94a3b8", italic=True)
        self._string_format = self._build_format("#86efac")
        self._def_format = self._build_format("#facc15")
        self._class_format = self._build_format("#fdba74")

        self._add_rule(self._compile_words(self._KEYWORDS), keyword_fmt)
        self._add_rule(self._compile_words(self._BUILTINS), builtin_fmt)
        self._add_rule(QtCore.QRegularExpression(r"\b0[xX][0-9a-fA-F]+\b"), number_fmt)
        self._add_rule(QtCore.QRegularExpression(r"\b\d+\.\d+\b"), number_fmt)
        self._add_rule(QtCore.QRegularExpression(r"\b\d+\b"), number_fmt)
        self._add_rule(QtCore.QRegularExpression(r"'[^'\\]*(?:\\.[^'\\]*)*'"), self._string_format)
        self._add_rule(QtCore.QRegularExpression(r'"[^"\\]*(?:\\.[^"\\]*)*"'), self._string_format)
        self._add_rule(QtCore.QRegularExpression(r"#.*"), comment_fmt)
        self._add_rule(QtCore.QRegularExpression(r"@[\w\.]+"), decorator_fmt)
        keywords_pattern = "|".join(sorted(set(self._KEYWORDS)))
        self._add_rule(
            QtCore.QRegularExpression(
                rf"(?<!\S)(?!(?:{keywords_pattern})\b)([A-Za-z_]\w*)(?=\s*=\s*(?![=]))"
            ),
            variable_fmt,
        )

        self._function_regex = QtCore.QRegularExpression(r"\bdef\s+([A-Za-z_]\w*)")
        self._class_regex = QtCore.QRegularExpression(r"\bclass\s+([A-Za-z_]\w*)")

        self._triple_single = QtCore.QRegularExpression("'''")
        self._triple_double = QtCore.QRegularExpression('"""')

    def _compile_words(self, words: tuple[str, ...]) -> QtCore.QRegularExpression:
        joined = "|".join(sorted(set(words)))
        return QtCore.QRegularExpression(rf"\b({joined})\b")

    def _add_rule(
        self, expression: QtCore.QRegularExpression, text_format: QtGui.QTextCharFormat
    ) -> None:
        self._rules.append((expression, text_format))

    def _build_format(
        self, color: str, *, bold: bool = False, italic: bool = False
    ) -> QtGui.QTextCharFormat:
        fmt = QtGui.QTextCharFormat()
        fmt.setForeground(QtGui.QColor(color))
        if bold:
            fmt.setFontWeight(QtGui.QFont.Bold)
        if italic:
            fmt.setFontItalic(True)
        return fmt

    def highlightBlock(self, text: str) -> None:  # pragma: no cover - GUI feature
        for regex, text_format in self._rules:
            match = regex.globalMatch(text)
            while match.hasNext():
                m = match.next()
                start = m.capturedStart()
                length = m.capturedLength()
                if start >= 0 and length > 0:
                    self.setFormat(start, length, text_format)

        self._highlight_named_group(self._function_regex, text, self._def_format)
        self._highlight_named_group(self._class_regex, text, self._class_format)

        self.setCurrentBlockState(0)
        if not self._match_multiline(text, self._triple_single, 1):
            self._match_multiline(text, self._triple_double, 2)

    def _highlight_named_group(
        self,
        regex: QtCore.QRegularExpression,
        text: str,
        text_format: QtGui.QTextCharFormat,
        group: int = 1,
    ) -> None:
        match = regex.globalMatch(text)
        while match.hasNext():
            m = match.next()
            start = m.capturedStart(group)
            length = m.capturedLength(group)
            if start >= 0 and length > 0:
                self.setFormat(start, length, text_format)

    def _match_multiline(
        self,
        text: str,
        delimiter: QtCore.QRegularExpression,
        state: int,
    ) -> bool:
        start = 0
        add = 0
        if self.previousBlockState() != state:
            match = delimiter.match(text)
            start = match.capturedStart()
            add = match.capturedLength()
        while start >= 0:
            match = delimiter.match(text, start + add)
            end = match.capturedStart()
            if end >= 0:
                length = end - start + match.capturedLength()
                self.setFormat(start, length, self._string_format)
                match = delimiter.match(text, start + length)
                start = match.capturedStart()
            else:
                self.setFormat(start, len(text) - start, self._string_format)
                self.setCurrentBlockState(state)
                return True
        return False
