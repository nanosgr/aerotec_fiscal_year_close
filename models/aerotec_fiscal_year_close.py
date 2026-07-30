import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Tipos de cuenta contable (account.account.account_type) que se consideran
# de resultado (ingresos/egresos) a efectos del cierre.
RESULT_ACCOUNT_TYPES = (
    "income",
    "income_other",
    "expense",
    "expense_depreciation",
    "expense_direct_cost",
)

# Tipos de cuenta contable que se consideran patrimoniales (activo, pasivo y
# patrimonio neto) a efectos de la refundición de balance.
BALANCE_ACCOUNT_TYPES = (
    "asset_receivable",
    "asset_cash",
    "asset_current",
    "asset_non_current",
    "asset_prepayments",
    "asset_fixed",
    "liability_payable",
    "liability_credit_card",
    "liability_current",
    "liability_non_current",
    "equity",
    "equity_unaffected",
)


class AerotecFiscalYearClose(models.Model):
    _name = "aerotec.fiscal.year.close"
    _description = "Cierre de Ejercicio Contable"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_to desc, company_id, id desc"
    _check_company_auto = True

    name = fields.Char(
        string="Referencia",
        required=True,
        readonly=True,
        default="Nuevo",
        tracking=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Empresa",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
    )
    date_from = fields.Date(
        string="Inicio del Ejercicio",
        required=True,
        tracking=True,
    )
    date_to = fields.Date(
        string="Fecha de Cierre",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        selection=[("draft", "Borrador"), ("closed", "Cerrado")],
        string="Estado",
        default="draft",
        required=True,
        tracking=True,
    )
    closing_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Diario de Cierre",
        required=True,
        default=lambda self: self.env.company.closing_journal_id,
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
        tracking=True,
    )
    result_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Cuenta Resultados del Ejercicio",
        required=True,
        domain="[('code', '=like', '3%'), ('company_ids', 'in', [company_id]), ('deprecated', '=', False)]",
        tracking=True,
    )
    result_move_id = fields.Many2one(
        comodel_name="account.move",
        string="Asiento: Cierre de Resultados",
        readonly=True,
        copy=False,
    )
    balance_move_id = fields.Many2one(
        comodel_name="account.move",
        string="Asiento: Refundición de Balance",
        readonly=True,
        copy=False,
    )
    opening_move_id = fields.Many2one(
        comodel_name="account.move",
        string="Asiento: Apertura del Ejercicio Siguiente",
        readonly=True,
        copy=False,
    )
    notes = fields.Text(string="Notas")

    # -------------------------------------------------------------------------
    # Constraints
    # -------------------------------------------------------------------------

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for rec in self:
            if rec.date_from >= rec.date_to:
                raise ValidationError(
                    _("La fecha de inicio debe ser anterior a la fecha de cierre.")
                )

    @api.constrains("date_from", "date_to", "company_id", "state")
    def _check_no_overlap(self):
        for rec in self:
            domain = [
                ("id", "!=", rec.id),
                ("company_id", "=", rec.company_id.id),
                ("state", "=", "closed"),
                ("date_from", "<=", rec.date_to),
                ("date_to", ">=", rec.date_from),
            ]
            if self.search_count(domain):
                raise ValidationError(
                    _(
                        "Ya existe un cierre cerrado para %(company)s que se solapa con el período %(from)s - %(to)s.",
                        company=rec.company_id.name,
                        **{"from": rec.date_from, "to": rec.date_to},
                    )
                )

    # -------------------------------------------------------------------------
    # Default / onCreate
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "Nuevo") == "Nuevo":
                # La secuencia no usa "use_date_range", por lo que el
                # parámetro sequence_date de next_by_code no tiene efecto
                # sobre el %(year)s del prefijo: hay que fijarlo vía contexto.
                vals["name"] = (
                    self.env["ir.sequence"]
                    .with_context(ir_sequence_date=vals.get("date_to"))
                    .next_by_code("aerotec.fiscal.year.close")
                    or "Nuevo"
                )
            if not vals.get("closing_journal_id"):
                # El campo tiene un default basado en self.env.company, que
                # sólo resuelve la empresa "activa" del usuario. En un alta
                # multiempresa (p.ej. creación por código, importación, o
                # cuando company_id difiere de la empresa activa) hay que
                # resolver el diario contra la empresa propia del registro.
                company = (
                    self.env["res.company"].browse(vals["company_id"])
                    if vals.get("company_id")
                    else self.env.company
                )
                vals["closing_journal_id"] = company.closing_journal_id.id
        return super().create(vals_list)

    @api.onchange("company_id")
    def _onchange_company_id(self):
        if self.company_id:
            self.closing_journal_id = self.company_id.closing_journal_id
            self.result_account_id = self.company_id.fiscal_year_result_account_id

    # -------------------------------------------------------------------------
    # Acción principal: Ejecutar Cierre
    # -------------------------------------------------------------------------

    def action_close(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("El cierre ya fue ejecutado."))
        self._validate_before_close()
        with self.env.cr.savepoint():
            self._close_income_expense_accounts()
            self._close_balance_sheet_accounts()
            self._lock_fiscal_year()
            self._create_opening_move()
            self.state = "closed"
        _logger.info(
            "Cierre de ejercicio %s ejecutado para empresa %s",
            self.name,
            self.company_id.name,
        )
        return True

    def _validate_before_close(self):
        if not self.closing_journal_id:
            raise UserError(
                _("Debe configurar un diario de cierre para la empresa %s.")
                % self.company_id.name
            )
        if not self.result_account_id:
            raise UserError(
                _("Debe configurar la cuenta de Resultados del Ejercicio para la empresa %s.")
                % self.company_id.name
            )

    # -------------------------------------------------------------------------
    # Paso 1: Cierre de cuentas de resultado (4.x, 5.x)
    # -------------------------------------------------------------------------

    def _close_income_expense_accounts(self):
        """
        Debita/acredita las cuentas de resultado (income, income_other, expense,
        expense_depreciation, expense_direct_cost) según su account_type.
        La diferencia neta va a la cuenta de Resultados del Ejercicio.
        """
        balances = self._get_account_balances_by_type(
            account_types=RESULT_ACCOUNT_TYPES,
            date_from=self.date_from,
            date_to=self.date_to,
        )
        if not balances:
            _logger.warning(
                "No se encontraron saldos en cuentas de resultado para el cierre %s.",
                self.name,
            )

        move_lines = []
        total_balance = 0.0
        for account, balance in balances.items():
            if abs(balance) < 0.01:
                continue
            total_balance += balance
            if balance > 0:
                # Saldo deudor (típico egresos): acreditar para zerizar
                move_lines.append(
                    {
                        "account_id": account.id,
                        "name": _("Cierre de resultados - %s") % account.code,
                        "debit": 0.0,
                        "credit": balance,
                    }
                )
            else:
                # Saldo acreedor (típico ingresos): debitar para zerizar
                move_lines.append(
                    {
                        "account_id": account.id,
                        "name": _("Cierre de resultados - %s") % account.code,
                        "debit": -balance,
                        "credit": 0.0,
                    }
                )

        # Contrapartida en la cuenta Resultados del Ejercicio
        # total_balance < 0 → ganancia → credit al resultado
        # total_balance > 0 → pérdida → debit al resultado
        if abs(total_balance) >= 0.01:
            if total_balance < 0:
                move_lines.append(
                    {
                        "account_id": self.result_account_id.id,
                        "name": _("Resultado del ejercicio %(from)s/%(to)s")
                        % {"from": self.date_from.year, "to": self.date_to.year},
                        "debit": 0.0,
                        "credit": -total_balance,
                    }
                )
            else:
                move_lines.append(
                    {
                        "account_id": self.result_account_id.id,
                        "name": _("Resultado del ejercicio %(from)s/%(to)s")
                        % {"from": self.date_from.year, "to": self.date_to.year},
                        "debit": total_balance,
                        "credit": 0.0,
                    }
                )

        if not move_lines:
            return

        move = self._create_and_post_move(
            date=self.date_to,
            ref=_("Cierre de cuentas de resultado - %s") % self.name,
            lines=move_lines,
        )
        self.result_move_id = move

    # -------------------------------------------------------------------------
    # Paso 2: Refundición de cuentas de balance (1.x, 2.x, 3.x)
    # -------------------------------------------------------------------------

    def _close_balance_sheet_accounts(self):
        """
        Lleva a cero las cuentas de activo, pasivo y patrimonio neto (incluye
        equity_unaffected, donde suele acumularse el resultado del ejercicio).
        Cuentas con saldo deudor → se acreditan; con saldo acreedor → se debitan.
        La ecuación contable garantiza que el asiento cuadre.
        """
        # Saldos acumulados hasta date_to (incluye el asiento del paso 1)
        balances = self._get_account_balances_by_type(
            account_types=BALANCE_ACCOUNT_TYPES,
            date_from=None,
            date_to=self.date_to,
        )

        move_lines = []
        for account, balance in balances.items():
            if abs(balance) < 0.01:
                continue
            if balance > 0:
                # Saldo deudor (activos): acreditar para zerizar
                move_lines.append(
                    {
                        "account_id": account.id,
                        "name": _("Refundición de balance - %s") % account.code,
                        "debit": 0.0,
                        "credit": balance,
                    }
                )
            else:
                # Saldo acreedor (pasivos, patrimonio): debitar para zerizar
                move_lines.append(
                    {
                        "account_id": account.id,
                        "name": _("Refundición de balance - %s") % account.code,
                        "debit": -balance,
                        "credit": 0.0,
                    }
                )

        if not move_lines:
            _logger.warning(
                "No se encontraron saldos de balance para refundir en el cierre %s.",
                self.name,
            )
            return

        move = self._create_and_post_move(
            date=self.date_to,
            ref=_("Refundición de cuentas de balance - %s") % self.name,
            lines=move_lines,
        )
        self.balance_move_id = move

    # -------------------------------------------------------------------------
    # Paso 3: Bloqueo del ejercicio
    # -------------------------------------------------------------------------

    def _lock_fiscal_year(self):
        self.company_id.sudo().fiscalyear_lock_date = self.date_to

    # -------------------------------------------------------------------------
    # Paso 4: Asiento de apertura del ejercicio siguiente
    # -------------------------------------------------------------------------

    def _create_opening_move(self):
        """
        Crea el asiento de apertura del ejercicio siguiente como inversión exacta
        del asiento de refundición de balance.
        """
        if not self.balance_move_id:
            return

        opening_date = self.date_to + timedelta(days=1)
        opening_lines = []
        for line in self.balance_move_id.line_ids:
            opening_lines.append(
                {
                    "account_id": line.account_id.id,
                    "name": _("Apertura de ejercicio - %s") % line.account_id.code,
                    # Invertir débito/crédito respecto al asiento de cierre
                    "debit": line.credit,
                    "credit": line.debit,
                }
            )

        if not opening_lines:
            return

        move = self._create_and_post_move(
            date=opening_date,
            ref=_("Apertura del ejercicio %s") % opening_date.year,
            lines=opening_lines,
        )
        self.opening_move_id = move

    # -------------------------------------------------------------------------
    # Acción de reversión
    # -------------------------------------------------------------------------

    def action_reverse(self):
        self.ensure_one()
        if self.state != "closed":
            raise UserError(_("Solo se puede revertir un cierre ejecutado."))
        self._validate_before_reverse()

        # 1. Limpiar el lock primero para poder cancelar los asientos
        prev_lock = self._get_previous_lock_date()
        self.company_id.sudo().fiscalyear_lock_date = prev_lock

        # 2. Cancelar y eliminar asientos en orden inverso
        for move_fname in ("opening_move_id", "balance_move_id", "result_move_id"):
            move = self[move_fname]
            if move:
                move.button_draft()
                move.unlink()
            self[move_fname] = False

        self.state = "draft"
        _logger.info(
            "Cierre %s revertido para empresa %s",
            self.name,
            self.company_id.name,
        )
        return True

    def _validate_before_reverse(self):
        later_close = self.search(
            [
                ("company_id", "=", self.company_id.id),
                ("state", "=", "closed"),
                ("date_to", ">", self.date_to),
            ],
            limit=1,
        )
        if later_close:
            raise UserError(
                _(
                    "No se puede revertir este cierre porque existe un cierre posterior: %s."
                )
                % later_close.name
            )

    def _get_previous_lock_date(self):
        """Retorna la fecha de lock del cierre anterior, o False si no existe."""
        prev_close = self.search(
            [
                ("company_id", "=", self.company_id.id),
                ("state", "=", "closed"),
                ("date_to", "<", self.date_to),
            ],
            order="date_to desc",
            limit=1,
        )
        return prev_close.date_to if prev_close else False

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _get_account_balances_by_type(self, account_types, date_from, date_to):
        """
        Retorna un dict {account_record: balance} para las cuentas cuyo
        account_type esté incluido en account_types.
        Si date_from es None, acumula desde el inicio de la historia contable.
        """
        domain = [
            ("company_id", "=", self.company_id.id),
            ("move_id.state", "=", "posted"),
            ("date", "<=", date_to),
            ("account_id.account_type", "in", list(account_types)),
        ]
        if date_from:
            domain.append(("date", ">=", date_from))

        groups = (
            self.env["account.move.line"]
            .with_company(self.company_id)
            ._read_group(
                domain=domain,
                groupby=["account_id"],
                aggregates=["debit:sum", "credit:sum"],
            )
        )
        result = {}
        for account, debit_sum, credit_sum in groups:
            balance = (debit_sum or 0.0) - (credit_sum or 0.0)
            if abs(balance) >= 0.01:
                result[account] = balance
        return result

    def _create_and_post_move(self, date, ref, lines):
        """Crea y postea un asiento contable, devuelve el record."""
        move_vals = {
            "date": date,
            "ref": ref,
            "journal_id": self.closing_journal_id.id,
            "company_id": self.company_id.id,
            "move_type": "entry",
            "line_ids": [(0, 0, line) for line in lines],
        }
        move = self.env["account.move"].with_company(self.company_id).create(move_vals)
        move.action_post()
        return move
