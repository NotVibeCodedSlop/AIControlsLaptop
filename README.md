# AIControlsLaptop
This is kinda a guide for how to setup my stream fo chat controls laptop and the ai controls laptop thingies.

## 1. prerequisites
You will need to have a Linux box.
A web cam (scrcpy is enough).
Some money (to burn AI tokens with or to spend electricity costs of your local model on) (optional).
Git clone'd this repo `git clone https://github.com/NotVibeCodedSlop/AIControlsLaptop.git`
Tech experience.
And a rooted Android phone (magisk).
## 2. Setup
So on your rooted Android phone enable Zygisk and grab the [HID Gadget Module by kelexine](https://github.com/kelexine/hid-gadget-module).
Then install Termux and copy one python script to your /sdcard dir (and maybe copy the bashrc and the sh script to termux for autostart)
Then copy all of them into your termux home dir.
Now get the IP of your phone and connect both devices to the same LAN.

## 3. Run
Now you can use one of 3 things:
- testhid.html (a web browser based thing to just remote control).
- The chatcontrols/ (npm install, npm run) thingy.
- or aicontrolslaptop.py (and put your api key or ollama thingy there).
