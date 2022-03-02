aiocflib
========

`aiocflib` is a Python library that allows communication with
[Crazyflie](https://bitcraze.io) drones using an async-friendly API instead of
the traditional callback-based API of the [official Crazyflie client
library](https://pypi.org/project/cfclient).

The API of `aiocflib` is roughly based on the official Crazyflie client
library, but they are not fully API-compatible (even if you ignore the
differences between async and sync functions). The primary goal of this
library is to allow working with Crazyflie drones from heavily concurrent
command-line apps and scripts that are based on an `asyncio` or Trio-based
event loop, without getting stuck in callback hell. API compatibility is not
a goal, and neither is feature parity with the official Crazyflie library.

The library adheres to semantic versioning.

Documentation
-------------

The documentation is still mostly in the docstrings; they are fairly
comprehensive, but there is no user guide yet and there are no tutorials. If
you would like to help out, send a pull request or join our [Discord
server](https://skybrush.io/r/discord) and let us know what you would like to
work on.

License
-------

`aiocflib` is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

`aiocflib` is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <https://www.gnu.org/licenses/>.
