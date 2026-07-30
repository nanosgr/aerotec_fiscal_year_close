from odoo import fields, models

CLOSING_JOURNAL_CODE = "AP_CI"
CLOSING_JOURNAL_NAME = "Apertura / Cierre Ejercicio"


class ResCompany(models.Model):
    _inherit = "res.company"

    closing_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Diario de Cierre",
        domain="[('type', '=', 'general'), ('company_id', '=', id)]",
        help="Diario utilizado para registrar los asientos de cierre y apertura del ejercicio.",
    )
    fiscal_year_result_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Cuenta Resultados del Ejercicio",
        domain="[('code', '=like', '3%'), ('company_ids', 'in', [id]), ('deprecated', '=', False)]",
        help="Cuenta de patrimonio neto (3.x.x) donde se acumula el resultado del ejercicio al cierre.",
    )

    def _register_hook(self):
        """Garantiza que cada empresa tenga un diario de cierre configurado.

        Se resuelve acá (y no en un ``post_init_hook``/script de migración)
        porque este método corre recién cuando el registro de modelos está
        completamente armado, evitando romper por campos ``required`` que
        agregan otros módulos (p.ej. ``discriminate_taxes`` de l10n_ar_ux) y
        que todavía no estén cargados en fases más tempranas del arranque.
        Es idempotente: se re-ejecuta en cada carga del registro pero no
        hace nada en empresas que ya tienen ``closing_journal_id``, y
        reutiliza un diario existente con el mismo código en vez de
        duplicarlo.
        """
        res = super()._register_hook()
        journal_model = self.env["account.journal"]
        for company in self.env["res.company"].search([]):
            if company.closing_journal_id:
                continue
            journal = journal_model.search(
                [
                    ("company_id", "=", company.id),
                    ("code", "=", CLOSING_JOURNAL_CODE),
                    ("type", "=", "general"),
                ],
                limit=1,
            )
            if not journal:
                vals = {
                    "name": CLOSING_JOURNAL_NAME,
                    "code": CLOSING_JOURNAL_CODE,
                    "type": "general",
                    "company_id": company.id,
                }
                if "discriminate_taxes" in journal_model._fields:
                    vals["discriminate_taxes"] = "no"
                journal = journal_model.create(vals)
            company.closing_journal_id = journal
        return res
