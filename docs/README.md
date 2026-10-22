# MBUDDY documentation

Start with the project overview in the [main README](../README.md).

## Get it running
- [Getting started](GETTING_STARTED.md): what each part needs, download sizes, and the one-command setup
- [Full installation, with the desk device](setup/INSTALLATION.md)
- [The phone app](setup/PHONE_APP.md) and [the cloud brain with the phone](setup/CLOUD_BRAIN_AND_PHONE.md)
- [Running everything locally](LOCAL_AI_GUIDE.md)

## The three parts
- [The PC app](PC_APP.md): the spaces and features
- [Using the phone app](USER_GUIDE.md): every screen and what to say
- [The desk device](HARDWARE.md): parts, wiring, flashing

## How it works
- [System overview](architecture/SYSTEM_OVERVIEW.md), [architecture map](architecture/ARCHITECTURE_MAP.md), [inner life](architecture/INNER_LIFE.md)
- [API reference](api/API.md), [developer quick reference](development/QUICK_REFERENCE.md)
- Security: the local server's `/control/*` routes only check that a request comes from this PC. Don't expose the
  server beyond localhost (a tunnel would bypass that check); see [SECURITY.md](../SECURITY.md).

## Taking part
- [Contributing](../CONTRIBUTING.md), [code of conduct](../CODE_OF_CONDUCT.md), [governance](../GOVERNANCE.md), [security](../SECURITY.md)
- [Good first issues](GOOD_FIRST_ISSUES.md): small, real places to start
- [Build the phone app](BUILD.md), [how it is put together](ARCHITECTURE.md)
- [For maintainers](MAINTAINERS.md)
- [Third-party notices](../THIRD_PARTY_NOTICES.md), [changelog](../CHANGELOG.md), [licence](../LICENSE)
