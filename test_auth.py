# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Offline regression: an unset or incorrect proxy key must never grant access."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import single_account_tts


class AuthenticationTest(unittest.TestCase):
    def test_unset_key_fails_closed(self):
        with patch.object(single_account_tts, "LOCAL_KEY", ""):
            for supplied in (None, "Bearer ", "Bearer anything"):
                with self.subTest(supplied=supplied), self.assertRaises(HTTPException) as raised:
                    single_account_tts._guard(supplied)
                self.assertEqual(raised.exception.status_code, 401)

    def test_valid_key_only(self):
        with patch.object(single_account_tts, "LOCAL_KEY", "test-key"):
            single_account_tts._guard("Bearer test-key")
            for supplied in (None, "Bearer other", "test-key"):
                with self.subTest(supplied=supplied), self.assertRaises(HTTPException):
                    single_account_tts._guard(supplied)


if __name__ == "__main__":
    unittest.main()
