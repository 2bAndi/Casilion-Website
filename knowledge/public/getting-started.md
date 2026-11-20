# Getting started

> Placeholder documentation for the skeleton. Commands, option names and file paths are invented examples, not the real product. Replace this file with real documentation.

Cassilion runs as a single server process. This page takes you from download to a running node.

## Install

Download the build for your platform from the customer area and unpack it. The archive contains the server binary `cassilion`, a sample configuration file and the license agreement. No installer is needed.

## Place the license file

Download your license file `cassilion.lic` from the customer area and put it next to the configuration file. The Community license is free. Cassilion checks the file's signature offline; it never contacts a license server.

## First start

Start the server with `cassilion start --config cassilion.conf`. On first start Cassilion creates its data directory and writes the listening address to the log. Stop it with `cassilion stop`.

## Check the version

`cassilion --version` prints the version and build. Support always asks for this first.
