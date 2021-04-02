"""
MIT License

Copyright (c) 2020 Airbyte

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import copy
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Iterator, Mapping, MutableMapping, Tuple

from airbyte_protocol import (
    AirbyteCatalog,
    AirbyteConnectionStatus,
    AirbyteMessage,
    AirbyteRecordMessage,
    AirbyteStateMessage,
    AirbyteStream,
    ConfiguredAirbyteCatalog,
    ConfiguredAirbyteStream,
    Status,
    SyncMode,
)
from airbyte_protocol import Type as MessageType

from base_python.integration import Source
from base_python.logger import AirbyteLogger
from base_python.sdk.streams.core import Stream, IncrementalStream


class AbstractSource(Source, ABC):
    def __init__(self):
        super().__init__()

    @abstractmethod
    def check_connection(self, logger: AirbyteLogger, config: Mapping[str, Any]) -> Tuple[bool, Any]:
        """
        :return: A tuple of (boolean, error). If boolean is true, then we can connect to the underlying data source using the provided configuration.
        Otherwise, the input config cannot be used to connect to the underlying data source, and the "error" object should describe what went wrong.
        The error object will be cast to string to display the problem to the user.
        """

    @abstractmethod
    def streams(self, config: Mapping[str, Any] = None) -> Mapping[str, Stream]:
        """
        :return: A mapping from stream name to the stream class representing that stream
        """

    @property
    def name(self) -> str:
        """Source name"""
        return self.__class__.__name__

    def discover(self, logger: AirbyteLogger, config: Mapping[str, Any]) -> AirbyteCatalog:
        """Discover streams"""
        streams = []

        for name, stream in self.streams(config=config).items():
            args = {
                'name': name,
                'json_schema': stream.get_json_schema(),
            }

            supported_sync_modes = [SyncMode.full_refresh]
            if isinstance(stream, IncrementalStream):
                supported_sync_modes.append(SyncMode.incremental)
                args['source_defined_cursor'] = stream.source_defined_cursor
                args['default_cursor_field'] = [stream.cursor_field] if isinstance(stream.cursor_field, str) else stream.cursor_field

            args['supported_sync_modes'] = supported_sync_modes

            streams.append(AirbyteStream(**args))

        return AirbyteCatalog(streams=streams)

    def check(self, logger: AirbyteLogger, config: Mapping[str, Any]) -> AirbyteConnectionStatus:
        """Check connection"""
        alive, error = self.check_connection(logger, config)
        if not alive:
            return AirbyteConnectionStatus(status=Status.FAILED, message=str(error))

        return AirbyteConnectionStatus(status=Status.SUCCEEDED)

    def read(
            self, logger: AirbyteLogger, config: Mapping[str, Any], catalog: ConfiguredAirbyteCatalog, state: MutableMapping[str, Any] = None
    ) -> Iterator[AirbyteMessage]:

        state = state or {}
        total_state = copy.deepcopy(state)
        logger.info(f"Starting syncing {self.name}")
        # TODO assert all streams exist in the connector
        for configured_stream in catalog.streams:
            try:
                yield from self._read_stream(logger=logger, config=config, configured_stream=configured_stream, state=total_state)
            except Exception as e:
                logger.exception(f"Encountered an exception while reading stream {self.name}")
                raise e

        logger.info(f"Finished syncing {self.name}")

    def _read_stream(
            self, logger: AirbyteLogger, config: Mapping[str, Any], configured_stream: ConfiguredAirbyteStream, state: MutableMapping[str, Any]
    ) -> Iterator[AirbyteMessage]:
        stream_name = configured_stream.stream.name
        stream_instance = self.streams(config)[stream_name]
        use_incremental = configured_stream.sync_mode == SyncMode.incremental and isinstance(stream_instance, IncrementalStream)

        stream_state = {}
        if use_incremental and state.get(stream_name):
            logger.info(f"Set state of {stream_name} stream to {state.get(stream_name)}")
            stream_state = state.get(stream_name)

        logger.info(f"Syncing {stream_name} stream")
        record_counter = 0
        for record in stream_instance.read_stream(stream_state=copy.deepcopy(stream_state)):
            now = int(datetime.now().timestamp()) * 1000
            message = AirbyteRecordMessage(stream=stream_name, data=record, emitted_at=now)
            yield AirbyteMessage(type=MessageType.RECORD, record=message)
            record_counter += 1
            if use_incremental:
                stream_state = stream_instance.get_updated_state(stream_state, record)
                # TODO allow configuring checkpoint interval
                if stream_instance.continuously_save_state and record_counter % 100 == 0:
                    state[stream_name] = stream_state
                    yield AirbyteMessage(type=MessageType.STATE, state=AirbyteStateMessage(data=state))

        if use_incremental and stream_state:
            state[stream_name] = stream_state
            # output state object only together with other stream states
            yield AirbyteMessage(type=MessageType.STATE, state=AirbyteStateMessage(data=state))