# Troubleshooting

> Placeholder documentation for the skeleton. Commands, option names and file paths are invented examples, not the real product. Replace this file with real documentation.

The most common problems and where to look first.

## The server does not start

Read the last lines of the log. The usual causes are a data directory that is not writable, a port that is already in use, and a typo in the configuration file. `cassilion check-config cassilion.conf` validates the file without starting the server.

## Connection refused

Check that the server is running and that `network.listen` names an address the client can reach. The default listens on the local machine only.

## Slow startup

After an unclean shutdown Cassilion replays the commit log before it accepts connections. With a large commit log this takes time. The log shows the progress.

## High memory use

Set `cache.max_memory` to limit the cache. See the configuration page.
