# SPAN integration for HomeAssistant/HACS

[Home Assistant](https://www.home-assistant.io/) Integration for [Span smart panel](https://www.span.io/panel).

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

This integration should not have any negative impact on your Span installation, but as Span has not published a documented API, we cannot guarantee this will work for you, or that will not break as your panel is updated.
The author(s) will try to keep this integration working, but cannot provide technical support for either Span or your homes electrical system.

# Installation

1. Install [HACS](https://hacs.xyz/)
2. Go to HACS `Integrations >` section
3. In the uppper right click `...`
4. Select `Custom Repositories`
5. Set `Repository` to "gdgib/span-hacs" and `Category` to "Integration, then click `Add`
6. Add the "Span Panel" integration
7. Restart Home Assistant
7. In the Home Assistant UI go to `Settings`
8. Click `Devices & Services`
10. Click `+ Add Integration`
11. Search for "Span"

# Devices & Entities

This integration will a device for your span panel.
This devices will have entities for:

* Circuits
  * On/Off Switch
  * Priority Selector
  * Power Usage
* Network Connectivity (Wi-Fi, Wired, & Cellular)
* Door State

# License

This integration is published under the MIT license.

# Contributing & Issues

If you have a problem, feel free to [open an issue](https://github.com/gdgib/span-hacs/issues), but please know issues regarding your network, Span configuration, or home electrical system are outside of our purview.
For those capable, please consider opening even a low quality [pull request](https://github.com/gdgib/span-hacs/pulls) when possible, as we're generally very happy to have a starting point when making a change.
