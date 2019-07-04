import datetime, sys

from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *

from electroncash import bitcoin
from electroncash.address import Address
from electroncash.i18n import _
from electroncash_gui.qt.util import MessageBoxMixin, Buttons, HelpLabel
from electroncash_gui.qt.amountedit import MyLineEdit, BTCAmountEdit, AmountEdit
from .not_broken_paytoedit import PayToEdit # We used our own custom PayToEdit which came from non-broken Electron Cash. Electron Cash SLP has a broken impleentation.
import electroncash.web as web

from .constants import *

class PaymentDialog(QDialog, MessageBoxMixin):
    def __init__(self, window, plugin, payment_data):
        # We want to be a top-level window
        QDialog.__init__(self, parent=None)

        #print("PaymentDialog", "payment_data =", payment_data)
        self.payment_data = payment_data
        
        self.plugin = plugin

        # WARNING: Copying some attributes so PayToEdit() will work.
        self.main_window = window
        self.contacts = self.main_window.contacts
        self.completions = self.main_window.completions

        self.count_labels = [
            "Disabled",
            "Once",
            "Always",
        ]
        self.display_count_labels = [
            "Always",
        ]
        run_always_index = self.count_labels.index("Always")
        
        # NOTE: User entered data, for verification purposes (enabling save/create), and subsequent dispatch on button press.
        
        self.value_description = ""
        self.value_amount = None
        self.value_payto_outputs = []
        self.value_run_occurrences = self.count_labels.index("Always")
        self.set_flags(0 if self.payment_data is None else self.payment_data[PAYMENT_FLAGS])
        payment_was_fiat = False
        
        if self.payment_data is not None:
            self.value_description = self.payment_data[PAYMENT_DESCRIPTION]
            self.value_amount = abs(self.payment_data[PAYMENT_AMOUNT])
            payment_was_fiat = self.payment_data[PAYMENT_FLAGS] & PAYMENT_FLAG_AMOUNT_IS_FIAT
            self.value_run_occurrences = self.payment_data[PAYMENT_COUNT0]
        
        # NOTE: Set up the UI for this dialog.
        self.setMinimumWidth(500)
        if payment_data is None:
            self.setWindowTitle(_("Create New Scheduled Payment"))
        else:
            self.setWindowTitle(_("Edit Existing Scheduled Payment"))
            
        formLayout = QFormLayout()
        self.setLayout(formLayout)
        formLayout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        # Input fields.
        msg = _('Description of the payment (not mandatory).') + '\n\n' + _('The description is not sent to the recipient of the funds. It is stored in your wallet file, and displayed in the \'History\' tab.')
        self.description_label = HelpLabel(_('Description'), msg)
        self.description_edit = MyLineEdit()
        self.description_edit.setText(self.value_description)
        formLayout.addRow(self.description_label, self.description_edit)
        
        msg = _('How much to pay.') + '\n\n' + _('Unhelpful descriptive text')
        self.amount_label = HelpLabel(_('Amount'), msg)
        self.amount_e = BTCAmountEdit(window.get_decimal_point) # WARNING: This has to be named this, as PayToEdit accesses it.
        if not payment_was_fiat:
            self.amount_e.setAmount(self.value_amount)
        else:
            self.amount_e.setHidden(True)
        # WARNING: This needs to be present before PayToEdit is constructed (as that accesses it's attribute on this object),
        # but added to the layout after in order to try and reduce the "cleared amount" problem that happens when an address
        # is entered (perhaps on a selected completion, i.e. of a contact).
        
        # WARNING: This will clear the amount when an address is set, see PayToEdit.check_text.
        self.payto_edit = PayToEdit(self)
        self.payto_edit.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
        msg = _('Recipient of the funds.') + '\n\n' + _('You may enter a Bitcoin Cash address, a label from your list of contacts (a list of completions will be proposed), or an alias (email-like address that forwards to a Bitcoin Cash address)')
        payto_label = HelpLabel(_('Pay to'), msg)
        formLayout.addRow(payto_label, self.payto_edit)
        def set_payment_address(address):
            self.payto_edit.payto_address = bitcoin.TYPE_ADDRESS, Address.from_string(address)
            self.value_payto_outputs = self.payto_edit.get_outputs(False)
            contact_name = None
            if address in window.wallet.contacts.keys():
                contact_type, contact_name = window.wallet.contacts[address]
            if contact_name is not None:
                self.payto_edit.setText(contact_name +' <'+ address +'>')
            else:
                if Address.is_valid(address):
                    address = Address.from_string(address).to_ui_string()
                self.payto_edit.setText(address)                
        if payment_data is not None:
            set_payment_address(payment_data[PAYMENT_ADDRESS])

        completer = QCompleter()
        completer.setCaseSensitivity(False)
        self.payto_edit.setCompleter(completer)
        completer.setModel(self.completions)

        amount_hbox = QHBoxLayout()
        self.useFiatCheckbox = QCheckBox(_("Denomiate payment in FIAT rather than BCH"))
        self.useFiatCheckbox.setToolTip(_("If you elect to denomiate the payment in FIAT, then the BCH transaction\nuses the FIAT price at the time of payment to determine how much\nactual BCH is transmitted to your payee.") + "\n\n" +  _("Requirements") +":\n"+ _("1. You must have a fiat currency defined and a server enabled in settings.") +"\n"+ _("2. The fiat spot price quote must but be available from the FX server."+ "\n"+ _("If this checkbox is interactive and not disabled, these requirements are met.")))
        isFiatEnabled = self.plugin.can_do_fiat(self.main_window)
        self.useFiatCheckbox.setChecked(payment_was_fiat)
        self.useFiatCheckbox.setEnabled(isFiatEnabled)

        self.fiat_amount_e = AmountEdit(self.main_window.fx.get_currency if isFiatEnabled else '')
        self.fiat_amount_e.setHidden(not payment_was_fiat)
        if payment_was_fiat:
            self.fiat_amount_e.setText(str(self.value_amount))
        
        amount_hbox.addWidget(self.amount_e) # either this or fiat_amount_e are visible at any 1 time
        amount_hbox.addWidget(self.fiat_amount_e) # either this or amoune_e are visible at any 1 time
        amount_hbox.addWidget(self.useFiatCheckbox)
        
        def useFiatToggled(b):
            # Note we do it this explicit way because the order matters to avoid quick visual glitches as widgets
            # pop into and out of existence.  Hiding the visible one then revealing the invisible one is the best way
            # to avoid glitches.  The reverse causes a slight detectable spastication of the UI. :/  -Calin
            if b:
                self.amount_e.setHidden(True)
                self.fiat_amount_e.setHidden(False)
            else:
                self.fiat_amount_e.setHidden(True)
                self.amount_e.setHidden(False)
                
        
        self.useFiatCheckbox.toggled.connect(useFiatToggled)

        # WARNING: We created this before PayToEdit and add it to the layout after, due to the dependency issues with PayToEdit accessing `self.amount_e`.
        formLayout.addRow(self.amount_label, amount_hbox)
        
        if payment_data is not None:
            text = _("No payments made.")
            if payment_data[PAYMENT_DATELASTPAID] is not None:
                text = datetime.datetime.fromtimestamp(payment_data[PAYMENT_DATELASTPAID]).strftime("%c")
            textLabel = QLabel(text)
            label = HelpLabel(_('Last Paid'), _('Date last paid.') + '\n\n' + _('The date at which this scheduled payment was last meant to send a transaction to the network, which the user acted on'))
            formLayout.addRow(label, textLabel)
            
        count_combo = QComboBox()
        count_combo.addItems(self.display_count_labels)
        count_combo.setCurrentIndex(self.display_count_labels.index(self.count_labels[self.value_run_occurrences]))
        msg = _('Repeat') + '\n\n' + _('The number of times the payment should be made.')
        label = HelpLabel(_('Repeat'), msg)
        formLayout.addRow(label, count_combo)

        # The setting will be cleared if the wallet somehow becomes unencrypted, and will only be available for unencrypted wallets.
        isEnabled = not self.main_window.wallet.has_password() and window.config.fee_per_kb() is not None
        self.value_autopayment = self.value_autopayment and isEnabled
        # Will show it for now, for encrypted wallets.  Might be less confusing not to show it.
        options_hbox = QHBoxLayout()
        self.autoPaymentCheckbox = QCheckBox(_("Make this payment automatically"))
        self.autoPaymentCheckbox.setToolTip(_("Requirements") +":\n"+ _("1. The wallet must not have a password.") +"\n"+ _("2. There must be a default fee/kb configured for the wallet."+ "\n"+ _("If this checkbox is interactive and not disabled, these requirements are met.")))
        self.autoPaymentCheckbox.setChecked(self.value_autopayment)
        self.autoPaymentCheckbox.setEnabled(isEnabled)
        options_hbox.addWidget(self.autoPaymentCheckbox)
        
        formLayout.addRow(_("Options"), options_hbox)

        import importlib
        from . import when_widget
        importlib.reload(when_widget)
        self.whenWidget = when_widget.WhenWidget(_("When"))
        self.whenWidget.setWhen(None if payment_data is None else payment_data[PAYMENT_WHEN])
        formLayout.addRow(self.whenWidget)

        # NOTE: Hook up value events and provide handlers.
        
        def validate_input_values():
            allow_commit = True
            allow_commit = allow_commit and len(self.value_description) > 0
            allow_commit = allow_commit and self.value_amount is not None and self.value_amount > 0
            allow_commit = allow_commit and len(self.value_payto_outputs) > 0
            allow_commit = allow_commit and self.value_run_occurrences == run_always_index
            # allow_commit = allow_commit and self.value_run_occurrences > -1 and self.value_run_occurrences < len(count_labels)
            self.save_button.setEnabled(allow_commit)
                
        def on_run_occurrences_changed(unknown):
            self.value_run_occurrences = self.count_labels.index(self.display_count_labels[count_combo.currentIndex()])
            validate_input_values()
        count_combo.currentIndexChanged.connect(on_run_occurrences_changed)

        def on_recipient_changed():
            self.value_payto_outputs = self.payto_edit.get_outputs(False)
            validate_input_values()
        self.payto_edit.textChanged.connect(on_recipient_changed)
        
        def on_amount_changed():
            self.value_amount = self.amount_e.get_amount() if not self.useFiatCheckbox.isChecked() else float(self.fiat_amount_e.get_amount() or 0.00)
            validate_input_values()
        self.amount_e.textChanged.connect(on_amount_changed)
        self.fiat_amount_e.textChanged.connect(on_amount_changed)
        self.useFiatCheckbox.toggled.connect(on_amount_changed)

        def on_description_changed():
            self.value_description = self.description_edit.text().strip()
            validate_input_values()
        self.description_edit.textChanged.connect(on_description_changed)
        
        def on_autopayment_toggled(v):
            self.value_autopayment = v == Qt.Checked
        self.autoPaymentCheckbox.stateChanged.connect(on_autopayment_toggled)
        
        # Buttons at bottom right.
        save_button_text = _("Save")
        if payment_data is None:
            save_button_text = _("Create")
        self.save_button = b = QPushButton(save_button_text)
        b.clicked.connect(self.save)

        self.cancel_button = b = QPushButton(_("Cancel"))
        b.clicked.connect(self.close)
        b.setDefault(True)

        # pet peeve -- on macOS it's customary to have cancel on left, action-on-right in dialogs
        if sys.platform == 'darwin':
            self.buttons = [self.cancel_button, self.save_button]
        else:
            self.buttons = [self.save_button, self.cancel_button]
        
        hbox = QHBoxLayout()
        #hbox.addLayout(Buttons(*self.sharing_buttons))
        hbox.addStretch(1)
        hbox.addLayout(Buttons(*self.buttons))
        formLayout.addRow(hbox)

        validate_input_values()
        self.update()
        
    def save(self):
        # NOTE: This is in lieu of running some kind of updater that updates the esetimated time every second.
        if self.whenWidget.updateEstimatedTime():
            if not self.question(_("The next matching date passed between the last time you modified the date, and when you clicked on save.  Do you wish to proceed anyway?"), title=_("Next Matching Date Changed")):
                return
    
        data_id = None
        if self.payment_data is not None:
            data_id = self.payment_data[PAYMENT_ID]
            
        payment_data = [ None ] * PAYMENT_ENTRY_LENGTH
        payment_data[PAYMENT_ID] = data_id
        payment_data[PAYMENT_ADDRESS] = self.value_payto_outputs[0][1].to_storage_string()
        flags = self.get_flags() # may include the new PAYMENT_FLAG_AMOUNT_IS_FIAT
        if flags & PAYMENT_FLAG_AMOUNT_IS_FIAT:
            payment_data[PAYMENT_AMOUNT] = -self.value_amount # NEW! Negative amounts indicate a fiat payment -- this is a hack to maintain backwards compat. with older plugin
        else:
            payment_data[PAYMENT_AMOUNT] = self.value_amount
        payment_data[PAYMENT_DESCRIPTION] = self.value_description
        payment_data[PAYMENT_COUNT0] = self.value_run_occurrences
        payment_data[PAYMENT_WHEN] = self.whenWidget.getWhen().toText()
        payment_data[PAYMENT_DATENEXTPAID] = self.whenWidget.getEstimatedTime()
        payment_data[PAYMENT_FLAGS] = flags
                
        wallet_name = self.main_window.wallet.basename()
        self.plugin.update_payment(wallet_name, payment_data)
        
        self.close()
        
    def closeEvent(self, event):
        wallet_name = self.main_window.wallet.basename()
        if self.payment_data is None:
            payment_id = None
        else:
            payment_id = self.payment_data[PAYMENT_ID]
        self.plugin.on_payment_editor_closed(wallet_name, payment_id)
        event.accept()
        
    def onTimeChanged(self, clock_current_time):
        self.whenWidget.updateEstimatedTime(currentTime=clock_current_time)
        
    def get_flags(self):
        flags = 0
        if self.value_autopayment:
            flags |= PAYMENT_FLAG_AUTOPAY
        if self.useFiatCheckbox.isChecked():
            flags |= PAYMENT_FLAG_AMOUNT_IS_FIAT
        return flags
        
    def set_flags(self, flags):
        self.value_autopayment = flags & PAYMENT_FLAG_AUTOPAY == PAYMENT_FLAG_AUTOPAY

    def lock_amount(self, flag): # WARNING: Copied as needed for PayToEdit
        self.amount_e.setFrozen(flag)
        
    def do_update_fee(self): # WARNING: Copied as needed for PayToEdit
        pass

    def pay_to_URI(self, URI): # WARNING: Copied as needed for PayToEdit
        if not URI:
            return
        try:
            out = web.parse_URI(URI, self.on_pr)
        except Exception as e:
            self.show_error(_('Invalid bitcoincash URI:') + '\n' + str(e))
            return
        r = out.get('r')
        sig = out.get('sig')
        name = out.get('name')
        if r or (name and sig):
            self.prepare_for_payment_request()
            return
        address = out.get('address')
        amount = out.get('amount')
        label = out.get('label')
        message = out.get('message')
        # use label as description (not BIP21 compliant)
        if label and not message:
            message = label
        if address:
            self.payto_edit.setText(address)
        if message:
            self.description_edit.setText(message)
        if amount:
            self.amount_e.setAmount(amount)
            self.amount_e.textEdited.emit("")

    def prepare_for_payment_request(self): # WARNING: Copied as needed for PayToEdit
        self.payto_edit.is_pr = True
        for e in [self.payto_edit, self.amount_e, self.description_edit]:
            e.setFrozen(True)
        self.payto_edit.setText(_("please wait..."))
        return True

    on_pr = None
