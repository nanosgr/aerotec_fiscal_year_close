from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    closing_journal_id = fields.Many2one(
        comodel_name="account.journal",
        related="company_id.closing_journal_id",
        string="Diario de Cierre",
        readonly=False,
    )
    fiscal_year_result_account_id = fields.Many2one(
        comodel_name="account.account",
        related="company_id.fiscal_year_result_account_id",
        string="Cuenta Resultados del Ejercicio",
        readonly=False,
    )
