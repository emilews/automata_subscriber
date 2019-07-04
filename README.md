# Automata Subscriber - Electron Cash Plugin #
  
## Calin Contribution's ##
This is a continuation of Roger Taylor's original Scheduled Payment plugin available [here](https://github.com/rt121212121/electron_cash_scheduled_payments_plugin).  In addition to what Roger did, I modified his plugin to have the following:

  - Fixed "last payment" not working on auto-pay (now it works)
  - Added support for denominating payments in FIAT amounts, which are computed to BCH at actual time of payment.
  - Various bugfixes to the UI
  - Support for multiple payees in 1 tx in 'Send' tab if paying multiple missed payments at once
  - Added proper Cash Address handling/support.  Before everyting was Legacy addresses -- now the UI displays the right address depending on your prefs
  - Payments are not marked as "paid" until an actual transaction goes out paying the payments, either from the "Send" tab or from the autopay system. (Before, you would click "Pay" and it would mark them as paid, even if no tx was s
  ent.)
  - Various other miscellaneous bugfixes.


This is licensed under the MIT open source license.

![Screenshot](https://github.com/emilews/automata_subscriber/raw/master/screenshot.png)

## Donate ##

If you wish to encourage further development on things that matter to you, or even just show your appreciation, please feel free to donate to:

  ### Roger: `bitcoincash:qqg34gn7xfrd7yr7xjuklarptxn0xqg9tgexm2zu9z` ###

![Donate_Roger](https://github.com/emilews/automata_subscriber/raw/master/donate.png)

  ### Calin:  `bitcoincash:qphax4s4n9h60jxj2fkrjs35w2tvgd4wzvf52cgtzc` ###

![Donate_Calin](https://github.com/emilews/automata_subscriber/raw/master/donate_calin.png)

  ### Emilio:   `bitcoincash:qrkzr0n0xnw5c97fydjssmq3qu3wng2twq0s062wf5` ###
![Donate_Emilio](https://github.com/emilews/automata_subscriber/raw/master/donate_emilio.png)

## Installation ##

1. Download the [latest reviewed version](https://github.com/emilews/automata_subscriber/releases).
2. Get the latest version of the Electron Cash code from github (make sure it has the Plugin Manager that allows addition of plugins).
3. Either select `add plugin` or drag the zip file onto the plugin manager window.
4. It will be installed, and enabled.

## Security Warning ##

  ### Authors
I, Roger Taylor (rt121212121), and I, Calin Culianu (cculianu) authors of this plugin, affirm that there is no malicious code intentionally added by either of us to this plugin.  If you obtain this plugin from any source other than this github repository, proceed at your own risk!

The reason this needs to be said, is that an enabled Electron Cash plugin has almost complete access and potential control over any wallets that are open.

  ### New Developer
I, Emilio Wong (emilews), subsequent developer of this plugin, confirm that either Roger or Calin did not put any malicious code in here, as well as I affirm that my contributions and modifications are, indeed, following Taylor's and Culianu's path, as I neither put any malicious code or have the motivation to do it.

That said, bugs may happen, and for that I have no liability as you downloaded this plugin by your own will, and I didn't force you to do so.

## Usage ##

Once you have the plugin installed and enabled, you may use it.

1. Select the `Subscriptions` tab.
2. Right click in the list window, and select the `New subscription` item.
3. A dialog will appear that allows you to construct a scheduled payment.  It will estimate the next time that payment will be made, to help you visualise how your choice of when the payment will be made, will play out.  Select `Create` when you have filled out all the fields.
4. Wait until that new payment's next payment time passes.

## Known Issues ##

* The fake clock is not correctly hooked up to the payment scheduler.  So it does work, but.. it's not obvious how it works.  Note that due payments are only detected once every real time minute, at 1 second past each minute.  But the time used for detection is always the current selected clock, whether real or fake.
