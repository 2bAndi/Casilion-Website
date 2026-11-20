# Configuration

> Placeholder documentation for the skeleton. Commands, option names and file paths are invented examples, not the real product. Replace this file with real documentation.

Cassilion reads one configuration file at start. Options are grouped into sections. A changed file takes effect after a restart.

## Memory

Cassilion keeps recently used data in a cache. The option `cache.max_memory` limits the memory used for caching, for example `cache.max_memory = 2G`. On small machines set it to about half of the available memory. Without the option Cassilion uses up to 60 % of the machine's memory. To see the current use, run `cassilion status --memory`.

## Storage

`storage.data_dir` sets where data files live and `storage.log_dir` where the commit log goes. Put them on different disks when you can. Cassilion refuses to start if the data directory is not writable.

## Network

`network.listen` sets the address and port for client connections, for example `network.listen = 0.0.0.0:9470`. `network.tls` turns on encrypted connections and needs `network.cert_file` and `network.key_file`.

## Logging

`log.level` accepts `error`, `warn`, `info` and `debug`. Use `debug` only while investigating a problem; it writes a lot.
