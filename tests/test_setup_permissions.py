import unittest
import discord
from setup_permissions import grantable_permissions
class SetupPermissionsTest(unittest.TestCase):
    def test_does_not_request_unavailable_flags(self):
        requested=discord.Permissions(view_channel=True,send_messages=True,stream=True)
        result=grantable_permissions(requested,discord.Permissions(view_channel=True,send_messages=True))
        self.assertTrue(result.send_messages)
        self.assertFalse(result.stream)
        self.assertEqual(grantable_permissions(requested,discord.Permissions(administrator=True)).value,requested.value)
