# Scheduled Payments - Electron Cash Plugin #

This is licensed under the MIT open source license.

![Screenshot](https://github.com/cculianu/electron_cash_scheduled_payments_plugin/raw/master/screenshot.png)

## Donate ##

If you wish to encourage further development on things that matter to you, or even just show your appreciation, please feel free to donate to:

  bitcoincash:qqg34gn7xfrd7yr7xjuklarptxn0xqg9tgexm2zu9z
  
![Screenshot](https://github.com/cculianu/electron_cash_scheduled_payments_plugin/raw/master/donate.png)

## Installation ##

1. Download the [latest reviewed version](https://github.com/cculianu/electron_cash_scheduled_payments_plugin/releases/download/1.1-pre1/scheduled_payments.zip).
2. Get the latest version of the Electron Cash code from github (make sure it has the Plugin Manager that allows addition of plugins).
3. Either select `add plugin` or drag the zip file onto the plugin manager window.
4. It will be installed, and enabled.

## Security Warning ##

I, Roger Taylor (rt121212121), and I, Calin Culianu (cculianu) authors of this plugin, affirm that there is no malicious code intentionally added by either of us to this plugin.  If you obtain this plugin from any source other than this github repository, proceed at your own risk!

The reason this needs to be said, is that an enabled Electron Cash plugin has almost complete access and potential control over any wallets that are open.

## Usage ##

Once you have the plugin installed and enabled, you may use it.

1. Select the `Scheduled Payments` tab.
2. Right click in the list window, and select the `New scheduled payment` item.
3. A dialog will appear that allows you to construct a scheduled payment.  It will estimate the next time that payment will be made, to help you visualise how your choice of when the payment will be made, will play out.  Select `Create` when you have filled out all the fields.
4. Wait until that new payment's next payment time passes.

## Known Issues ##

* The fake clock is not correctly hooked up to the payment scheduler.  So it does work, but.. it's not obvious how it works.  Note that due payments are only detected once every real time minute, at 1 second past each minute.  But the time used for detection is always the current selected clock, whether real or fake.
* If you enter more than one address in the scheduled payment, who knows what will happen.  Only one address is stored.
