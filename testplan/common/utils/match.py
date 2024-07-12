"""
Module of utility types and functions that perform matching.
"""
import os
import re
import time
import warnings
from contextlib import closing
from typing import (
    AnyStr,
    Dict,
    List,
    Match,
    Optional,
    Pattern,
    Tuple,
    Union,
)

from typing_extensions import TypeAlias

from . import logger, timing
from .logfile import (
    LogPosition,
    LogStream,
    FileLogStream,
    BinaryFileLogStream,
    TextFileLogStream,
    RotatedFileLogStream,
    RotatedBinaryFileLogStream,
    RotatedTextFileLogStream,
    MTimeBasedLogRotationStrategy,
)

LOG_MATCHER_INTERVAL = 0.25
LOG_MATCHER_DEFAULT_TIMEOUT = 5.0


Regex: TypeAlias = Union[str, bytes, Pattern]


def _format_logline(s):
    if not s:
        return "<EOF>\n"
    if len(s) <= 100:
        return s
    return f"{s[:100]} ... ({len(s) - 100} chars omitted)"


def match_regexps_in_file(
    logpath: os.PathLike, log_extracts: List[Pattern]
) -> Tuple[bool, Dict[str, str], List[Pattern]]:
    """
    Return a boolean, dict pair indicating whether all log extracts matches,
    as well as any named groups they might have matched.

    :param logpath: Log file path.
    :param log_extracts:  Regex list.
    :return: Match result.
    """
    extracted_values = {}

    if not os.path.exists(logpath):
        return False, extracted_values, log_extracts

    extracts_status = [False for _ in log_extracts]

    # If log_extracts contain bytes regex, will convert all log_extracts to
    # bytes regex.
    if not all([isinstance(x.pattern, str) for x in log_extracts]):
        read_mode = "rb"
        _log_extracts = []
        for regex in log_extracts:
            if not isinstance(regex.pattern, bytes):
                _log_extracts.append(re.compile(regex.pattern.encode("utf-8")))
            else:
                _log_extracts.append(regex)
    else:
        read_mode = "r"
        _log_extracts = log_extracts

    with open(logpath, read_mode) as log:
        for line in log:
            for pos, regexp in enumerate(_log_extracts):
                match = regexp.match(line)
                if match:
                    extracted_values.update(match.groupdict())
                    extracts_status[pos] = True

    unmatched = [
        exc for idx, exc in enumerate(log_extracts) if not extracts_status[idx]
    ]
    return all(extracts_status), extracted_values, unmatched


class ScopedLogfileMatch:
    def __init__(
        self,
        log_matcher: "LogMatcher",
        regex: Regex,  # to be extended to accept list[Regex]
        timeout: float = LOG_MATCHER_DEFAULT_TIMEOUT,
    ):
        self.regex, self.timeout = regex, timeout
        self.match_results = []
        self.match_failure = None

        self.log_matcher = log_matcher

    def __enter__(self):
        self.log_matcher.seek_eof()
        self.match_results = []
        self.match_failure = None
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            return False
        m = self.log_matcher.match(
            self.regex, self.timeout, raise_on_timeout=False
        )
        s_pos = self.log_matcher._debug_info_s[0]
        e_pos = self.log_matcher._debug_info_e[0]
        if m is not None:
            self.match_results.append((m, self.regex, s_pos, e_pos))
        else:
            self.match_failure = (None, self.regex, s_pos, e_pos)


class LogMatcher(logger.Loggable):
    """
    Single line matcher for text files (usually log files). Once matched, it
    remembers the line number of the match and subsequent matches are scanned
    from the current line number. This can be useful when matched lines are not
    unique for the entire log file. Support simple cases of log rotation
    """

    def __init__(
        self, log_path: Union[os.PathLike, str], binary: bool = False
    ):
        """
        :param log_path: Path to the log file. log_path can be a glob then LogMatcher support
               rotated logfiles, that matching to the glob.
        :param binary: if True the logfile treated as a binary file, and binary regexps need to be used
        """
        self.log_path = log_path
        self.binary = binary
        self.marks = {}
        self.position: Optional[LogPosition] = None
        self.log_stream: FileLogStream = self._create_log_stream()

        self._debug_info_s = ()
        self._debug_info_e = ()

        # deprecation helpers
        self.had_transformed = False

        super(LogMatcher, self).__init__()

    def _create_log_stream(self) -> LogStream:

        # as rotated logstream should be able to handle non-rotated streams
        # without overhead we use that by default, but give an envvar to be
        # able to turn it off. We not yet expose the possibility to select the
        # logstream through the api.

        if os.environ.get("TESTPLAN_NO_LOGROTATION_IN_LOGMATCHER") in (
            "true",
            "True",
            "1",
            "yes",
            "Yes",
        ):
            return self._create_non_rotated_log_stream()

        return self._create_rotated_log_stream()

    def _create_non_rotated_log_stream(self) -> FileLogStream:
        return (
            BinaryFileLogStream(self.log_path)
            if self.binary
            else TextFileLogStream(self.log_path)
        )

    def _create_rotated_log_stream(self) -> RotatedFileLogStream:
        return (
            RotatedBinaryFileLogStream(
                self.log_path, MTimeBasedLogRotationStrategy()
            )
            if self.binary
            else RotatedTextFileLogStream(
                self.log_path, MTimeBasedLogRotationStrategy()
            )
        )

    def _prepare_regexp(self, regexp: Regex) -> Pattern[AnyStr]:

        if isinstance(regexp, (str, bytes)):
            regexp = re.compile(regexp)
        try:
            if self.binary and isinstance(regexp.pattern, str):
                raise TypeError(
                    f"LogMatcher is configured for binary match but string regexp was provided. Pattern: {regexp}"
                )
            if not self.binary and isinstance(regexp.pattern, bytes):
                raise TypeError(
                    f"LogMatcher is configured for text match but bytes regexp was provided. Pattern: {regexp}"
                )
        except TypeError as error:
            if self.had_transformed:
                raise

            self.had_transformed = True
            self.binary = not self.binary
            self.log_stream = self._create_log_stream()
            warnings.warn(
                f"Incompatible regexp is used. "
                f"{error} "
                f"Transforming LogMatcher to {'binary' if self.binary else 'text'} "
                f"This fallback will be soon removed please update your LogMatcher or regexps to be in sync."
            )

        return regexp

    def seek(self, mark: Optional[str] = None):
        """
        Sets current file position to the specified mark. The mark has to exist.
        If the mark is None sets current file position to beginning of file.

        :param mark: Name of the mark.
        """
        if mark is None:
            self.position = None
        else:
            self.position = self.marks[mark]

    def seek_eof(self):
        """Sets current file position to the current end of file."""
        self.position = self.log_stream.flush()

    def seek_sof(self):
        """Sets current file position to the start of file."""
        self.seek()

    def mark(self, name: str):
        """
        Marks the current file position with the specified name. The mark name
        can later be used to set the file position

        :param name: Name of the mark.
        """
        self.marks[name] = self.position

    def _match(
        self,
        regex: Pattern[AnyStr],
        timeout: float,
    ) -> Optional[Match]:
        """
        Base block for ``match``, ``not_match`` & ``match_all``,
        as well as certain ``LogfileNamespace`` assertions.

        :param regex: Checked regular expression
        :param timeout: Timeout in seconds to wait for matching process,
            0 means matching till EOF and not waiting for new lines, any
            value greater than 0 means doing matching up to such seconds,
            defaults to 5 seconds
        :return: The regex match or None if no match is found
        """
        match = None
        start_time = time.time()
        end_time = start_time + timeout
        regex = self._prepare_regexp(regex)

        with closing(self.log_stream) as log:
            log.seek(self.position)

            non_eof = ""
            while True:
                line = log.readline()
                if self._debug_info_s is None:
                    self._debug_info_s = (
                        str(self.position)
                        if self.position is not None
                        else "<BOF>",
                        start_time,
                        _format_logline(line),
                    )

                if line:
                    non_eof = line
                    match = regex.match(line)
                    if match:
                        break
                elif timeout > 0:
                    time.sleep(LOG_MATCHER_INTERVAL)
                else:
                    break

                if timeout > 0 and time.time() > end_time:
                    break

            self.position = self.log_stream.position
            if self._debug_info_e is None:
                self._debug_info_e = (
                    str(self.position),
                    time.time(),
                    _format_logline(non_eof),
                )

        return match

    def match(
        self,
        regex: Regex,
        timeout: float = LOG_MATCHER_DEFAULT_TIMEOUT,
        raise_on_timeout: bool = True,
    ) -> Optional[Match]:
        """
        Matches each line in the log file from the current line number to the
        end of the file. If a match is found the line number is stored and the
        match is returned. By default an exception is raised if no match is
        found.

        :param regex: Regex string or compiled regular expression
            (``re.compile``)
        :param timeout: Timeout in seconds to wait for matching process,
            0 means matching till EOF and not waiting for new lines, any
            value greater than 0 means doing matching up to such seconds,
            defaults to 5 seconds
        :param raise_on_timeout: To raise TimeoutException or not
        :return: The regex match or None if no match is found
        """
        self._debug_info_s = None
        self._debug_info_e = None
        regex = self._prepare_regexp(regex)

        m = self._match(regex, timeout=timeout)

        if m is None:
            self.logger.debug(
                "%s: no expected match[%s] found,\nsearch starting from %s (around %s), "
                "where first line seen as:\n%s"
                "and ending at %s (around %s), where last line seen as:\n%s",
                self,
                regex.pattern,
                *self._debug_info_s,
                *self._debug_info_e,
            )
            if timeout and raise_on_timeout:
                raise timing.TimeoutException(
                    "No match[%s] found in %.2fs.", regex.pattern, timeout
                )

        return m

    def not_match(
        self,
        regex: Regex,
        timeout: float = LOG_MATCHER_DEFAULT_TIMEOUT,
    ):
        """
        Opposite of :py:meth:`~testplan.common.utils.match.LogMatcher.match`
        which raises an exception if a match is found. Matching is performed
        from the current file position. If match is not found within timeout
        period then no exception is raised.

        :param regex: Regex string or compiled regular expression
            (``re.compile``)
        :param timeout: Timeout in seconds to wait for matching process,
            0 means should not wait and return whatever matched on initial
            scan, defaults to 5 seconds
        """
        self._debug_info_s = None
        self._debug_info_e = None
        regex = self._prepare_regexp(regex)

        m = self._match(regex, timeout)

        if m is not None:
            self.logger.debug(
                "%s: unexpected match[%s] found,\nsearch starting from %s (around %s), "
                "where first line seen as:\n%s"
                "and ending at %s (around %s), where last line seen as:\n%s",
                self,
                regex.pattern,
                *self._debug_info_s,
                *self._debug_info_e,
            )
            raise Exception(
                f"Unexpected match[{regex.pattern}] found in {timeout}s"
            )

    def match_all(
        self,
        regex: Regex,
        timeout: float = LOG_MATCHER_DEFAULT_TIMEOUT,
        raise_on_timeout: bool = True,
    ) -> List[Match]:
        """
        Similar to match, but returns all occurrences of regex. By default an
        exception is raised if no match is found.

        :param regex: Regex string or compiled regular expression
            (``re.compile``)
        :param timeout: Timeout in seconds to find out all matches in file,
            defaults to 5 seconds.
        :param raise_on_timeout: To raise TimeoutException or not
        :return: A list of regex matches
        """
        matches = []
        end_time = time.time() + timeout

        self._debug_info_s = None
        self._debug_info_e = None
        regex = self._prepare_regexp(regex)

        while True:
            if timeout == 0:
                t = 0
            else:
                t = end_time - time.time()
                if t <= 0:
                    break
            m = self._match(regex, t)
            if m is not None:
                self._debug_info_e = None
                matches.append(m)
            else:
                break

        if not matches:
            self.logger.debug(
                "%s: no expected match[%s] found,\nsearch starting from %s (around %s), "
                "where first line seen as:\n%s"
                "and ending at %s (around %s), where last line seen as:\n%s",
                self,
                regex.pattern,
                *self._debug_info_s,
                *self._debug_info_e,
            )
            if timeout and raise_on_timeout:
                raise timing.TimeoutException(
                    "No match[%s] found in %.2fs.", regex.pattern, timeout
                )

        return matches

    def match_between(self, regex: Regex, mark1: str, mark2: str):
        """
        Matches file against passed in regex. Matching is performed from
        file position denoted by mark1 and ends before file position denoted
        by mark2. If a match is not found then None is returned.

        :param regex: regex string or compiled regular expression
            (``re.compile``)
        :param mark1: mark name of start position (None for beginning of file)
        :param mark2: mark name of end position
        :return: The regex match or None if no match is found
        """
        match = None

        regex = self._prepare_regexp(regex)

        with closing(self.log_stream) as log:
            log.seek(self.marks[mark1] if mark1 is not None else None)
            endpos = self.marks[mark2]
            while not self.log_stream.reached_position(endpos):
                line = log.readline()
                if not line:
                    break
                match = regex.match(line)
                if match:
                    break

        return match

    def not_match_between(self, regex: Regex, mark1: str, mark2: str):
        """
        Opposite of :py:meth:`~testplan.common.utils.match.LogMatcher.match_between`
        which returns None if a match is not found. Matching is performed
        from file position denoted by mark1 and ends before file position
        denoted by mark2. If a match is found then False is returned otherwise True.

        :param regex: regex string or compiled regular expression
            (``re.compile``)
        :param mark1: mark name of start position (None for beginning of file)
        :param mark2: mark name of end position
        """

        return not self.match_between(regex, mark1, mark2)

    def get_between(self, mark1=None, mark2=None):
        """
        Returns the content of the file from the start marker to the end marker.
        It is possible to omit either marker to receive everything from start
        to end of file.

        .. note::

            Since markers point to the byte position immediately after match,
            this function will not return what was matched for mark1, but will
            return the contents of what was matched for mark2.

        :param mark1: mark name of start position (None for beginning of file)
        :type mark1: ``str``
        :param mark2: mark name of end position (None for end of file)
        :type mark2: ``str``
        :return: The content between mark1 and mark2.
        :rtype: ``str``
        """
        if mark1 is not None and mark2 is not None:
            if (
                self.log_stream.compare(self.marks[mark1], self.marks[mark2])
                >= 0
            ):
                raise ValueError(
                    'Mark "{}" must be present before mark "{}"'.format(
                        mark1, mark2
                    )
                )

        with closing(self.log_stream) as log:
            start_pos = self.marks[mark1] if mark1 is not None else None
            end_pos = self.marks[mark2] if mark2 is not None else None
            log.seek(start_pos)
            if not end_pos:
                return log.read()
            lines_between = []
            while not self.log_stream.reached_position(end_pos):
                line = log.readline()
                lines_between.append(line)
            separator = b"" if self.binary else ""
            return separator.join(lines_between)

    def expect(
        self,
        regex: Regex,
        timeout: float = LOG_MATCHER_DEFAULT_TIMEOUT,
    ):
        """
        Context manager as a composite of
        :py:meth:`~testplan.common.utils.match.LogMatcher.seek_eof` and
        :py:meth:`~testplan.common.utils.match.LogMatcher.match`.
        On entering seeking to log stream EOF, on exiting doing log matching,
        as expected pattern should be (indirectly) produced by context manager
        body.

        :param regex: Regex string or compiled regular expression.
        :param timeout: Timeout in seconds as a float for regex matching.
        """
        return ScopedLogfileMatch(
            log_matcher=self,
            regex=regex,
            timeout=timeout,
        )

    def __str__(self) -> str:
        return f"LogMatcher[{self.log_path}]"
