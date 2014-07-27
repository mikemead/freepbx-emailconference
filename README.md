# FreePBX-EmailConf

FreePBX-EmailConf provides a simple extension to FreePBX allowing users to create conference rooms by simply sending a blank email to a dedicated conferencing email address.

## Contents

- [Getting started] (#getting-started)
- [How does it work?] (#how-does-it-work)
- [Supported platforms] (#supported-platforms)

## Getting started

### Requirements

- Python 2.7
- MySQL-Python

### Quick start

1. `git clone https://github.com/mikemead/freepbx-emailconf.git`
2. Update `conferences.conf` with your settings
3. Setup a cron job to run conferences.py regularly

### Detailed instructions

See [Supported platforms] (#supported-platforms) for instructions specific to your setup.

## How does it work?

FreePBX-EmailConf will login to an IMAP enabled mailbox to check for new emails, create conference rooms and then email the sender back with the details of the conference room.

## Supported platforms

| Platform       | Version     | Tested | Instructions |
| ---------------|-------------|--------|--------------|
| Elastix        | 2.4.0       | Y      |              |
| Elastix        | 2.4.0 x64   | N      |              |
| FreePBX Distro | 5.211       | N      |              |
| FreePBX Distro | 5.211 x64   | N      |              |