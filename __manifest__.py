{
    "name": "Aerotec - Cierre de Ejercicios Contables",
    "version": "18.0.1.1.0",
    "category": "Accounting/Accounting",
    "summary": "Gestión del cierre de ejercicios contables en esquema multiempresa",
    "author": "Sebastian Rios",
    "license": "LGPL-3",
    "depends": ["account_accountant", "mail"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/sequences.xml",
        "views/res_config_settings_views.xml",
        "views/aerotec_fiscal_year_close_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "auto_install": False,
}
