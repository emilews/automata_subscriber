import uuid, time, weakref, binascii, os, inspect

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from electroncash.address import Address
from electroncash.i18n import _
from electroncash.bitcoin import TYPE_ADDRESS, COIN
from electroncash.plugins import BasePlugin, hook
from electroncash.util import user_dir, NotEnoughFunds, ExcessiveFee
import electroncash.version

from . import scheduler
from . import when
from .constants import *
from .util import *


class SchedulerThreadJob:
    def __init__(self, plugin):
        self.last_second_time = time.time()
        self.last_minute_time = scheduler.roundTimeSeconds(time.time())

        self.plugin = weakref.proxy(plugin)

    def run(self):
        thread_current_time = time.time()
        if thread_current_time - self.last_second_time > 1.0:
            self.last_second_time += 1.0

            if self.plugin.clock_window is not None:
                self.plugin.clock_window.onTimeChanged(thread_current_time, self.plugin.clock)

            clock_current_time = self.plugin.clock.getTime()
            for dialog in self.plugin.weak_dialogs:
                if hasattr(dialog, "onTimeChanged"):
                    dialog.onTimeChanged(clock_current_time)

        # NOTE: This will not work (correctly) with the fake clock, as minutes pass faster.
        if thread_current_time - self.last_minute_time > 60.0:
            self.last_minute_time += 60.0

            clock_current_time = self.plugin.clock.getTime()
            self.plugin.signal_dummy.due_payments_signal.emit(clock_current_time)


class SignalDummy(QObject):
    due_payments_signal = pyqtSignal([int])


class Plugin(BasePlugin):
    electrumcash_qt_gui = None

    # There's no real user-friendly way to enforce this.  So for now, we just calculate it, and ignore it.
    is_version_compatible = True

    def __init__(self, parent, config, name):
        BasePlugin.__init__(self, parent, config, name)

        # Global settings for the plugin can be stored here.  It is not encrypted howerver.
        self.config = config

        self.wallet_windows = {}
        self.wallet_payment_tabs = {}
        self.wallet_payment_lists = {}
        self.wallet_payment_action_dialogs = {}
        self.wallet_payment_editor_dialogs = {}
        self.wallet_data = {}

        self.weak_dialogs = weakref.WeakSet()
        self.clock_window = None

        self.change_clock(real=True)

        self.job = SchedulerThreadJob(self)

        self.signal_dummy = SignalDummy()
        self.signal_dummy.due_payments_signal.connect(self.on_due_payments_signal)

    def on_due_payments_signal(self, clock_current_time):
        for wallet_name in self.get_open_wallet_names():
            self.process_due_payments(wallet_name, current_time=clock_current_time)

    def fullname(self):
        return 'Scheduled Payments'

    def description(self):
        return _("Schedule payments, in some way")

    def is_available(self):
        if self.is_version_compatible is None:
            version = MINIMUM_ELECTRON_CASH_VERSION
            try:
                version = float(electroncash.version.PACKAGE_VERSION) # doesn't always work.  WTF Roger. :p
            except ValueError:
                pass
            self.is_version_compatible = version >= MINIMUM_ELECTRON_CASH_VERSION
        return True

    def thread_jobs(self):
        return [
            self.job,
        ]

    def on_close(self):
        """
        BasePlugin callback called when the wallet is disabled among other things.
        """
        for window in list(self.wallet_windows.values()):
            self.close_wallet(window.wallet)

        self.close_clock_window()

    @hook
    def update_contact(self, address, new_entry, old_entry):
        print("update_contact", address, new_entry, old_entry)

    @hook
    def delete_contacts(self, contact_entries):
        print("delete_contacts", contact_entries)

    @hook
    def init_qt(self, qt_gui):
        """
        Hook called when a plugin is loaded (or enabled).
        """
        self.electrumcash_qt_gui = qt_gui
        # We get this multiple times.  Only handle it once, if unhandled.
        if len(self.wallet_windows):
            return

        # These are per-wallet windows.
        for window in self.electrumcash_qt_gui.windows:
            self.load_wallet(window.wallet, window)

    @hook
    def load_wallet(self, wallet, window):
        """
        Hook called when a wallet is loaded and a window opened for it.
        """
        wallet_name = window.wallet.basename()
        self.wallet_windows[wallet_name] = window

        self.add_ui_for_wallet(wallet_name, window)
        self.load_data_for_wallet(wallet_name, window)
        self.process_due_payments(wallet_name, on_wallet_loaded=True)
        self.refresh_ui_for_wallet(wallet_name)

        if False and len(self.wallet_windows) == 1:
            self.open_clock_window()

    @hook
    def close_wallet(self, wallet):
        wallet_name = wallet.basename()
        window = self.wallet_windows[wallet_name]
        del self.wallet_windows[wallet_name]

        self.remove_ui_for_wallet(wallet_name, window)
        self.unload_data_for_wallet(wallet_name)

        if len(self.wallet_windows) == 0:
            self.close_clock_window()

        # remove our enqueued "will possibly pay" payments
        to_del = { k for k,v in self.will_possibly_pay.items() if v[0] == wallet_name }
        for k in to_del: self.will_possibly_pay.pop(k, None)

    def get_due_payments_for_wallet(self, wallet_name, current_time):
        matches = []
        for payment_data in self.get_wallet_payments(wallet_name):
            if payment_data[PAYMENT_DATENEXTPAID] <= current_time:
                matches.append(payment_data)
        return matches

    def get_due_payments(self, current_time):
        matches = []
        for wallet_name in self.get_open_wallet_names():
            wallet_matches = self.get_due_payments_for_wallet(wallet_name, current_time)
            if len(wallet_matches):
                matches.append(wallet_matches)
        return matches

    def process_due_payments(self, wallet_name, current_time=None, on_wallet_loaded=False):
        """ When a wallet is loaded, detect if payments have become overdue. """
        if current_time is None:
            current_time = self.clock.getTime()

        due_payment_entries = self.get_due_payments_for_wallet(wallet_name, current_time)
        if not len(due_payment_entries):
            return

        deferred_results = []
        for payment_data in due_payment_entries:
            result = self.dispatch_due_payment(wallet_name, payment_data, current_time, defer_for_batching=True)
            if result is not None:
                deferred_results.append(result)

        wallet_data = self.wallet_data[wallet_name]
        wallet_data.save()


        if len(deferred_results):
            txid = self.autopay_payments(wallet_name, deferred_results, current_time)
            # Note if the payments were deferred for some reason, because payment failed.
            if txid is None:
                deferred_results = []


        # This is already done by the wallet loading code.
        if not on_wallet_loaded:
            self.refresh_ui_for_wallet(wallet_name)

        window = self.wallet_windows[wallet_name]
        paid_payment_ids = { payment_data[PAYMENT_ID] for payment_data, amount in deferred_results }
        if len(paid_payment_ids) > 0:
            s = wallet_name +": "
            if len(paid_payment_ids) == 1:
                s += _("1 scheduled payment was made.")
            else:
                s += _("%d scheduled payments were made.") % len(paid_payment_ids)
            window.notify(s)
        due_payment_ids = { payment_data[PAYMENT_ID] for payment_data in due_payment_entries }.difference(paid_payment_ids)
        if len(due_payment_ids) > 0:
            s = wallet_name +": "
            if len(due_payment_ids) == 1:
                s += _("1 scheduled payment became due.")
            else:
                s += _("%d scheduled payments became due.") % len(due_payment_ids)
            s += " "+ _("Check the scheduled payments tab.")
            window.notify(s)

    def dispatch_due_payment(self, wallet_name, payment_data, current_time, defer_for_batching=False):
        """ Either automatically pay, or put into overdue status, a due payment. """
        deferred_result = None
        payment_when = when.When.fromText(payment_data[PAYMENT_WHEN])
        relevant_start_times = []
        if payment_data[PAYMENT_DATELASTPAID] is not None:
            relevant_start_times.append(payment_data[PAYMENT_DATELASTPAID])
        relevant_start_times.append(payment_data[PAYMENT_DATEUPDATED])
        estimation_start_time = max(relevant_start_times)
        estimator = scheduler.WhenEstimator(estimation_start_time, payment_when)
        overdue_payment_times = estimator.getNextOccurrences(maxMatches=100, maxTime=current_time)
        if self.should_autopay_payment(wallet_name, payment_data):
            if defer_for_batching:
                deferred_result = payment_data, overdue_payment_times
            else:
                # If this fails, the payments will have become overdue instead.
                self.autopay_payments(wallet_name, [(payment_data, overdue_payment_times)], current_time)
        else:
            self.remember_overdue_payment_occurrences( payment_data, overdue_payment_times)
        # This sets the new time marker for what is considered overdue.
        payment_data[PAYMENT_DATEUPDATED] = current_time
        # Calculate the time of the next payment in the future.
        estimator = scheduler.WhenEstimator(current_time, payment_when)
        future_payment_times = estimator.getNextOccurrences(maxMatches=1)
        payment_data[PAYMENT_DATENEXTPAID] = future_payment_times[0]
        return deferred_result

    def should_autopay_payment(self, wallet_name, payment_data):
        """ Whether a payment in a wallet should be paid automatically, rather than simply marked as an unpaid occurrence. """
        window = self.wallet_windows.get(wallet_name, None)
        if not window.wallet.has_password() and window.config.fee_per_kb() is not None:
            if payment_data[PAYMENT_FLAGS] is not None:
                return payment_data[PAYMENT_FLAGS] & PAYMENT_FLAG_AUTOPAY == PAYMENT_FLAG_AUTOPAY
        return False

    def autopay_payments(self, wallet_name, payment_entries, current_time):
        """ For unencrypted wallets, the option is (will be) there to make the payments automatically, rather than simply mark them as unpaid occurrences. """
        # payment_entries = [ (payment_data, overdue_payment_times), ... ]

        wallet_window = self.wallet_windows[wallet_name]
        wallet = wallet_window.wallet
        config = wallet_window.config
        network = wallet_window.network

        outputs = []
        descs = []
        abortEarly = False
        for payment_data, overdue_payment_times in payment_entries:
            is_fiat = payment_data[PAYMENT_FLAGS] & PAYMENT_FLAG_AMOUNT_IS_FIAT
            if is_fiat:
                totalFiat = len(overdue_payment_times) * abs(payment_data[PAYMENT_AMOUNT])
                if not self.can_do_fiat(wallet_window):
                    wallet_window.show_error(_("Failed to automatically pay a Scheduled Payment:") + "\n" + _("Fiat Exchange data not available"))
                    abortEarly = True
                    break
                totalSatoshis = (totalFiat / float(wallet_window.fx.exchange_rate())) * COIN
            else:
                totalSatoshis = len(overdue_payment_times) * payment_data[PAYMENT_AMOUNT]
            if payment_data[PAYMENT_DESCRIPTION]:
                descs.append(payment_data[PAYMENT_DESCRIPTION])
            address = Address.from_string(payment_data[PAYMENT_ADDRESS])
            outputs.append((TYPE_ADDRESS, address, int(totalSatoshis)))

        password = None
        tx = None

        if not abortEarly:
            try:
                kwargs = {}
                argspec = inspect.getfullargspec(wallet.mktx)
                if 'sign_schnorr' in argspec.args:
                    # Support for schnorr signing Electron Cash 4.0.3+ -- note
                    # there was an API change between EC versions where method
                    # is_schnorr_enabled went from window to wallet
                    _is_schnorr_func = lambda: False
                    if hasattr(wallet, 'is_schnorr_enabled'):
                        _is_schnorr_func = wallet.is_schnorr_enabled
                    elif hasattr(wallet_window, 'is_schnorr_enabled'):
                        _is_schnorr_func = wallet_window.is_schnorr_enabled
                    kwargs['sign_schnorr'] = _is_schnorr_func()
                tx = wallet.mktx(outputs, password, config, **kwargs)
            except NotEnoughFunds:
                wallet_window.show_error(_("Failed to automatically pay a Scheduled Payment:") + "\n" + _("Insufficient funds"))
            except ExcessiveFee:
                wallet_window.show_error(_("Failed to automatically pay a Scheduled Payment:") + "\n" + _("Excessive Fee"))
            except BaseException as e:
                import traceback
                traceback.print_exc()
                self.print_error("Outputs:",outputs)
                wallet_window.show_error(_("Failed to automatically pay a Scheduled Payment:") + "\n" + (str(e) or "Unknown Error"))

            if tx:
                status, data = (None, None)

                if hasattr(network, 'broadcast_transaction'):
                    status, data = network.broadcast_transaction(tx)
                elif hasattr(network, 'broadcast'):
                    status, data = network.broadcast(tx)
                else:
                    # wtf. someone changed the API
                    data = _("Don't know how to broadcast a transaction. Are you on Electron Cash 3.2 or above?")

                if status:
                    # data is txid.
                    if data:
                        wallet.set_label(data, _("Scheduled payment") + ((": " + ', '.join(descs)) if descs else ''))
                        for payment_data, dummy in payment_entries:
                            payment_data[PAYMENT_DATELASTPAID] = current_time
                        self.wallet_data[wallet_name].save()

                    return data
                # data is error message
                wallet_window.show_error(_("Faiiled to automatically pay a Scheduled Payment:") + " " + str(data))

        # Fallback to remembering the overdue payments.
        # TODO: Alert the user about the failure - best way is to mark the payment.
        for payment_data, overdue_payment_times in payment_entries:
            self.remember_overdue_payment_occurrences( payment_data, overdue_payment_times)
        self.wallet_data[wallet_name].save() # remember didn't seem to work without calling this explicitly -Calin

    def remember_overdue_payment_occurrences(self, payment_data, overdue_payment_times):
        """ Record the newly identified overdue payment occurrences. """
        for overdue_payment_time in overdue_payment_times:
            if overdue_payment_time not in payment_data[PAYMENT_DATESOVERDUE]:
                payment_data[PAYMENT_DATESOVERDUE].append(overdue_payment_time)

    def check_payments_overdue(self, wallet_name, payment_ids):
        wallet_data = self.wallet_data[wallet_name]
        payment_entries = wallet_data.get(PAYMENT_DATA_KEY, [])

        for payment_data in payment_entries:
            if payment_data[PAYMENT_ID] in payment_ids and len(payment_data[PAYMENT_DATESOVERDUE]):
                return True
        return False

    @hook
    def set_label(self, wallet, addr_or_txid, label):
        ''' Catch when they actually paid -- wallet.set_label is called once a payment is done, passing us txid and the unique description
            we generated in prompt_pay_overdue_payment_occurrences() below.
            We use that unique description key to catch the payment that was made and thus know the payments were made, so
            we mark them as such. '''
        self.print_error("set_label called", wallet, addr_or_txid, label)
        vals = self.will_possibly_pay.pop(label, None)
        if vals:
            wallet_name, payment_keys = vals
            if wallet.basename() == wallet_name and not Address.is_valid(addr_or_txid):
                # matches
                self.print_error("Payment occurrence matched, forgetting: ",addr_or_txid,label,wallet_name,payment_keys)
                self.forget_overdue_payment_occurrences(wallet_name, payment_keys, mark_paid = True)
            else:
                self.print_error("No match, putting it back in our dict")
                # doesn't match, remember this thing
                self.will_possibly_pay[label] = vals

    will_possibly_pay = dict() # class-level dict of: tx_desc (containiing a unique key) -> (wallet_name, payment_occurrence_keys)

    def prompt_pay_overdue_payment_occurrences(self, wallet_name, payment_occurrence_keys):
        matches = self.match_overdue_payment_occurrences(wallet_name, payment_occurrence_keys)
        if not len(matches):
            return

        wallet_window = self.wallet_windows[wallet_name]
        wallet_window.show_send_tab()
        wallet_window.do_clear()
        wallet = wallet_window.wallet

        totalSatoshis = 0.0
        addresses = []
        amountStrs = dict()
        abortEarly = False
        f = ValueFormatter(wallet_window)

        for occurrence_count, payment_data in matches:
            amount = occurrence_count * abs(payment_data[PAYMENT_AMOUNT])
            is_fiat = payment_data[PAYMENT_FLAGS] & PAYMENT_FLAG_AMOUNT_IS_FIAT
            if is_fiat:
                if not self.can_do_fiat(wallet_window):
                    wallet_window.show_error(_("Payments contain Fiat amounts:") + " " + _("  (No FX rate available)"))
                    abortEarly = True
                    break
                amount = (amount / float(wallet_window.fx.exchange_rate())) * COIN
            totalSatoshis += amount
            address = payment_data[PAYMENT_ADDRESS]

            contact_name = None
            if address in wallet_window.contacts.keys():
                contact_type, contact_name = wallet_window.contacts[address]
            if contact_name is not None:
                addresses.append(contact_name +' <'+ address +'>')
            else:
                addresses.append(Address.from_string(address).to_ui_string())

            amountStrs[addresses[-1]] = f.format_value(amount, DISPLAY_AS_AMOUNT_NO_UNITS)

        if abortEarly:
            wallet_window.do_clear()
            return

        if len(addresses) > 1:
            wallet_window.payto_e.paytomany()
            wallet_window.payto_e.setText("\n".join([ add + ", " + amt for add, amt in amountStrs.items()]))
        else:
            wallet_window.payto_e.setText('\n'.join(addresses))
        wallet_window.amount_e.setAmount(totalSatoshis)
        wallet_window.payto_e.update_size()

        tx_extra = ' (ref:' + str(binascii.hexlify(os.urandom(8))).split("'")[1] + ')'

        if len(matches) == 1:
            match = matches[0]
            payment_data = match[1]
            wallet_window.message_e.setText( (payment_data[PAYMENT_DESCRIPTION].strip() or _("Scheduled payment")) + tx_extra)
        else:
            wallet_window.message_e.setText(_("Scheduled payments") + tx_extra)

        # freeze critical fields in the form, these get unfrozen when the user clicks "Clear" or do_clear() is
        # called during normal app operation.
        for e in [wallet_window.payto_e, wallet_window.amount_e, wallet_window.message_e]:
            e.setFrozen(True)
        wallet_window.max_button.setDisabled(True)

        tx_desc = wallet_window.message_e.text()

        self.will_possibly_pay[tx_desc] = (wallet_name, payment_occurrence_keys)


    def match_overdue_payment_occurrences(self, wallet_name, payment_occurrence_keys):
        wallet_data = self.wallet_data[wallet_name]
        payment_entries = wallet_data.get(PAYMENT_DATA_KEY, [])

        # Clear the overdue dates from any payments that have them.
        matches = []
        for payment_data in payment_entries:
            forget_count = 0
            occurrence_times = [ k[1] for k in payment_occurrence_keys if k[0] == payment_data[PAYMENT_ID] ]
            forget_times = []
            for forget_time in occurrence_times:
                if forget_time in payment_data[PAYMENT_DATESOVERDUE]:
                    forget_times.append(forget_time)
            if len(forget_times):
                matches.append( (len(forget_times), payment_data) )
        return matches


    def forget_overdue_payment_occurrences(self, wallet_name, payment_occurrence_keys, mark_paid=False):
        wallet_data = self.wallet_data[wallet_name]
        payment_entries = wallet_data.get(PAYMENT_DATA_KEY, [])

        # Clear the overdue dates from any payments that have them.
        matches = []
        for payment_data in payment_entries:
            forget_count = 0
            occurrence_times = [ k[1] for k in payment_occurrence_keys if k[0] == payment_data[PAYMENT_ID] ]
            forget_times = []
            for forget_time in occurrence_times:
                if forget_time in payment_data[PAYMENT_DATESOVERDUE]:
                    payment_data[PAYMENT_DATESOVERDUE].remove(forget_time)
                    forget_times.append(forget_time)
            if len(forget_times):
                if mark_paid:
                    payment_data[PAYMENT_DATELASTPAID] = max(forget_times)
                matches.append((len(forget_times), payment_data))

        wallet_data.save()
        self.refresh_ui_for_wallet(wallet_name)

        return matches


    def add_ui_for_wallet(self, wallet_name, window):
        from .payments_list import ScheduledPaymentsList
        l = ScheduledPaymentsList(window, self, wallet_name)

        tab = window.create_list_tab(l)
        self.wallet_payment_tabs[wallet_name] = tab
        self.wallet_payment_lists[wallet_name] = l
        if QFile.exists(":icons/clock5.svg"):
            # Electron Cash 4.0.6 and above use SVG icons for the clocks
            icon = QIcon(":icons/clock5.svg")
        else:
            icon = QIcon(":icons/clock5.png")
        window.tabs.addTab(tab, icon, _('Scheduled Payments'))

    def remove_ui_for_wallet(self, wallet_name, window):
        dialog = self.wallet_payment_action_dialogs.get(wallet_name, None)
        if dialog is not None:
            del self.wallet_payment_action_dialogs[wallet_name]
            dialog.close()

        payment_dialogs = self.wallet_payment_editor_dialogs.get(wallet_name, None)
        if payment_dialogs is not None:
            for payment_id in list(self.wallet_payment_editor_dialogs[wallet_name].keys()):
                dialog = self.wallet_payment_editor_dialogs[wallet_name][payment_id]
                del self.wallet_payment_editor_dialogs[wallet_name][payment_id]
                dialog.close()
            del self.wallet_payment_editor_dialogs[wallet_name]

        wallet_tab = self.wallet_payment_tabs.get(wallet_name, None)
        if wallet_tab is not None:
            del self.wallet_payment_lists[wallet_name]
            del self.wallet_payment_tabs[wallet_name]
            i = window.tabs.indexOf(wallet_tab)
            window.tabs.removeTab(i)

    def load_data_for_wallet(self, wallet_name, window):
        from .data_store import DataStore
        wallet_data = DataStore(window.wallet.storage)
        self.wallet_data[wallet_name] = wallet_data

        if False:
            # HACK TODO to trigger wallet open due payment detection case
            for payment_data in wallet_data.get(PAYMENT_DATA_KEY, []):
                payment_data[PAYMENT_DATESOVERDUE] = []
                payment_data[PAYMENT_DATEUPDATED] = 1525335600 - 10000
                payment_data[PAYMENT_DATENEXTPAID] = 1525335600

    def unload_data_for_wallet(self, wallet_name):
        wallet_data = self.wallet_data.get(wallet_name, None)
        if wallet_data is not None:
            del self.wallet_data[wallet_name]

    def refresh_ui_for_wallet(self, wallet_name):
        wallet_tab = self.wallet_payment_tabs[wallet_name]
        wallet_tab.update()
        wallet_tab = self.wallet_payment_lists[wallet_name]
        wallet_tab.update()

    def correct_payment_data(self, payment_data):
        if payment_data is None:
            return
        while len(payment_data) < PAYMENT_ENTRY_LENGTH:
            payment_data.append(None)

    def open_payment_editor(self, wallet_name, entry=None):
        payment_id = None
        if entry is not None:
            payment_id = entry[PAYMENT_ID]

        self.correct_payment_data(entry)

        dialog = None
        if  wallet_name in self.wallet_payment_editor_dialogs:
            if entry is not None and payment_id in self.wallet_payment_editor_dialogs[wallet_name]:
                dialog = self.wallet_payment_editor_dialogs[wallet_name][payment_id]
            elif payment_id is None:
                dialog = self.wallet_payment_editor_dialogs[wallet_name].get(payment_id, None)

        if dialog is None:
            window = self.wallet_windows[wallet_name]
            import importlib
            from . import payment_dialog
            importlib.reload(payment_dialog)
            dialog = payment_dialog.PaymentDialog(window, self, entry)
            self.weak_dialogs.add(dialog)
            if wallet_name not in self.wallet_payment_editor_dialogs:
                self.wallet_payment_editor_dialogs[wallet_name] = { payment_id: dialog }
            else:
                self.wallet_payment_editor_dialogs[wallet_name][payment_id] = dialog
            dialog.show()
        else:
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()

    def on_payment_editor_closed(self, wallet_name, payment_id):
        if wallet_name in self.wallet_payment_editor_dialogs and payment_id in self.wallet_payment_editor_dialogs[wallet_name]:
            del self.wallet_payment_editor_dialogs[wallet_name][payment_id]

    def open_payment_action_window(self, wallet_name, payment_ids, action):
        dialog = self.wallet_payment_action_dialogs.get(wallet_name, None)
        if dialog is None:
            window = self.wallet_windows[wallet_name]
            import importlib
            from . import payment_action_dialog
            importlib.reload(payment_action_dialog)
            dialog = payment_action_dialog.PaymentActionDialog(window, self, wallet_name, action, payment_ids)
            self.weak_dialogs.add(dialog)
            self.wallet_payment_action_dialogs[wallet_name] = dialog
            dialog.show()
        else:
            dialog.raise_()
            dialog.activateWindow()
            dialog.show()

    def on_payment_action_window_closed(self, wallet_name):
        if wallet_name in self.wallet_payment_action_dialogs:
            del self.wallet_payment_action_dialogs[wallet_name]

    def get_wallet(self, wallet_name):
        return self.wallet_windows[wallet_name].wallet

    def get_open_wallet_names(self):
        return list(self.wallet_windows.keys())

    def get_wallet_payments(self, wallet_name):
        """ Called by SchedularPaymentsList.on_update() when update() is called on it. """
        wallet_data = self.wallet_data[wallet_name]
        payment_entries = wallet_data.get(PAYMENT_DATA_KEY, [])
        return payment_entries

    def toggle_clock_window(self, wallet_name):
        if self.clock_window is None:
            self.open_clock_window()
        else:
            self.close_clock_window()

    def open_clock_window(self):
        if self.clock_window is None:
            import importlib
            from . import clock_window
            importlib.reload(clock_window)
            self.clock_window = clock_window.ClockWindow(self, _("Scheduled Payment Clock"))
            self.clock_window.show()

    def close_clock_window(self):
        if self.clock_window is not None:
            self.clock_window.close()
            self.clock_window = None

    def on_clock_window_closed(self, clock_window):
        """ Relayed notification by a window that is has received a close event. """
        if clock_window is self.clock_window:
            if not self.clock.isRealTime():
                self.change_clock(real=True)
            self.clock_window = None

    def change_clock(self, real=True):
        if real:
            self.clock = scheduler.RealClock()
        else:
            self.clock = scheduler.FakeClock(time.time())

    def open_create_payment_dialog(self, wallet_name):
        self.open_payment_editor(wallet_name)

    def open_edit_payment_dialog(self, wallet_name, payment_id):
        wallet_data = self.wallet_data[wallet_name]
        payment_entries = wallet_data.get(PAYMENT_DATA_KEY, [])

        target_entry = None
        for entry in payment_entries:
            if entry[PAYMENT_ID] == payment_id:
                target_entry = entry
                break

        if target_entry is not None:
            self.open_payment_editor(wallet_name, target_entry)

    def update_payment(self, wallet_name, payment_data):
        """
        Called by the scheduled payment dialog when a payment is created/or saved.
        """
        wallet_data = self.wallet_data[wallet_name]
        payment_entries = wallet_data.get(PAYMENT_DATA_KEY, [])

        self.correct_payment_data(payment_data)

        if payment_data[PAYMENT_ID] is None:
            # Finish initialising the new payment and add it to the list.
            payment_data[PAYMENT_ID] = uuid.uuid4().hex
            payment_data[PAYMENT_DATECREATED] = int(self.clock.getTime())
            payment_data[PAYMENT_DATESOVERDUE] = []
            payment_entries.append(payment_data)
        else:
            # Replace the old version with the new version.
            for i, entry in enumerate(payment_entries):
                if entry[PAYMENT_ID] == payment_data[PAYMENT_ID]:
                    payment_data[PAYMENT_DATECREATED] = entry[PAYMENT_DATECREATED]
                    payment_data[PAYMENT_DATELASTPAID] = entry[PAYMENT_DATELASTPAID]
                    payment_data[PAYMENT_DATESOVERDUE] = entry[PAYMENT_DATESOVERDUE]
                    payment_entries[i] = payment_data
                    break

        payment_data[PAYMENT_DATEUPDATED] = int(self.clock.getTime())

        wallet_data[PAYMENT_DATA_KEY] = payment_entries # This is expected to trigger the wallet data to save.
        self.refresh_ui_for_wallet(wallet_name)

    def delete_payments(self, wallet_name, payment_ids):
        wallet_data = self.wallet_data[wallet_name]
        payment_entries = wallet_data.get(PAYMENT_DATA_KEY, [])

        for entry in payment_entries[:]:
            if entry[PAYMENT_ID] in payment_ids:
                payment_entries.remove(entry)

        wallet_data[PAYMENT_DATA_KEY] = payment_entries # This is expected to trigger the wallet data to save.
        self.refresh_ui_for_wallet(wallet_name)

    def can_do_fiat(self, main_window=None):
        if main_window is None:
            main_window = self.wallet_windows.values()[0] if len(self.wallet_windows) else None
        return main_window and main_window.fx and main_window.fx.is_enabled() and main_window.fx.get_currency() and main_window.fx.exchange_rate() is not None
