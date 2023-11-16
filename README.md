# SPAN integration for HomeAssistant/HACS

[Home Assistant](https://www.home-assistant.io/) Integration for [Span smart panel](https://www.span.io/panel).

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

This integration should not have any negative impact on your Span installation, but as Span has not published a documented API, we cannot guarantee this will work for you, or that will not break as your panel is updated.
The author(s) will try to keep this integration working, but cannot provide technical support for either Span or your homes electrical system.

# Installation

1. Install [HACS](https://hacs.xyz/)
2. Go to HACS `Integrations` section
3. In the lower right click "Explore & Download Repositories"
4. Search for `Span`
5. Select the "Span Panel" result
6. Select "Download"
7. Restart Home Assistant
7. In the Home Assistant UI go to `Settings`
8. Click `Devices & Services`
10. Click `+ Add Integration`
11. Search for "Span"
12. Enter the IP of your Span Panel to begin setup

The setup flow has two methods available, either the "proximity" method where one presses the door button 3 times to "unlock" the panel's API, or a new method leveraging an authorization token.

### Method 1:
Simply open the door to your Span Panel and press the door sensor button 3 times in succession. The lights ringing the frame of your panel should blink momentarily, and the Panel will now be "unlocked" for 15 minutes (It may in fact be significantly longer, but 15 is the documented period.)

You can now simply proceed with the "Manual" flow in the UI menu.

_Please note that this method is scheduled by Span to be deprecated at an unknown point in the future in favor of Method 2_

### Method 2:

This method, at this time, will require some base knowledge of making direct calls to an API endpoint with JSON data via a CLI or custom tool. There is a long-term goal to eventually move this logic into the configuration UI behind the scenes, to avoid the need to do this manually yourself.

1. Make a POST to {Span_Panel_IP}`/api/v1/auth/register` with a JSON body of `{"name": "home-assistant-UNIQUEID", "description": "Home Assistant Local Span Integration"}`.

    * Use a unique value for UNIQUEID. Six random alphanumeric characters would be a reasonable choice. If the name conflicts with one that's already been created, then the request will fail.

2. If the panel is already "unlocked", you will get a 2xx response to this call containing the `"accessToken"`. If not, then you will be prompted to open and close the door of the panel 3 times, once every two seconds, and then retry the query.

3. Store the value from the `"accessToken"` property of the response. (It will be a long string, such as `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"` for example). This is the token which should be included with all future requests.

4. Send all future requests with the HTTP header `"Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"` (Remember, this is just a dummy example token!)

_(If you have multiple Span Panels, you will need to repeat this process for each panel, as tokens are only accepted by the panel that generated them.)_

You can now proceed with the "Auth Token" flow in the UI menu.

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

If you have a problem, feel free to [open an issue](https://github.com/gdgib/span/issues), but please know issues regarding your network, Span configuration, or home electrical system are outside of our purview.
For those capable, please consider opening even a low quality [pull request](https://github.com/gdgib/span/pulls) when possible, as we're generally very happy to have a starting point when making a change.
