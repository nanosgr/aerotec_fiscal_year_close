from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    closing_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Diario de Cierre",
        domain="[('type', '=', 'general'), ('company_id', '=', id)]",
        help="Diario utilizado para registrar los asientos de cierre y apertura del ejercicio.",
    )
    closing_result_prefixes = fields.Char(
        string="Prefijos Cuentas de Resultado",
        default="4,5",
        help=(
            "Prefijos del código contable que identifican las cuentas de resultado "
            "(ingresos y egresos). Separados por coma. Ejemplo: 4,5"
        ),
    )
    closing_balance_prefixes = fields.Char(
        string="Prefijos Cuentas de Balance",
        default="1,2,3",
        help=(
            "Prefijos del código contable que identifican las cuentas de activo, "
            "pasivo y patrimonio neto. Separados por coma. Ejemplo: 1,2,3"
        ),
    )
    fiscal_year_result_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Cuenta Resultados del Ejercicio",
        domain="[('code', '=like', '3%'), ('company_ids', 'in', [id]), ('deprecated', '=', False)]",
        help="Cuenta de patrimonio neto (3.x.x) donde se acumula el resultado del ejercicio al cierre.",
    )
