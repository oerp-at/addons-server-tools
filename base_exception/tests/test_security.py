# Copyright 2026 Weboffice IT und Marketing GmbH & CoKG
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

from odoo.tests import TransactionCase


class TestSecurity(TransactionCase):
    def test_admin_is_exception_manager(self):
        """Like Odoo's own manager groups, the admin user gets the exception
        manager group on install, so the exception rule menus are visible."""
        group = self.env.ref("base_exception.group_exception_rule_manager")
        admin = self.env.ref("base.user_admin")
        self.assertIn(group, admin.all_group_ids)
        menu = self.env.ref("base_exception.menu_action_exception")
        self.assertIn(
            menu.id, self.env["ir.ui.menu"].with_user(admin)._visible_menu_ids()
        )
