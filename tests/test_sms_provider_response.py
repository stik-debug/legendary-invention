"""Focused, dependency-light tests for Africa's Talking recipient responses."""
import unittest

import notify


class SmsProviderResponseTests(unittest.TestCase):
    def test_provider_acceptance_is_success(self):
        response = {
            'SMSMessageData': {
                'Message': 'Sent to 1/1 Total Cost: KES 0.8000',
                'Recipients': [{'statusCode': 101, 'status': 'Success', 'number': '+254700000000'}],
            }
        }
        self.assertTrue(notify._sms_response_status(response)[0])

    def test_recipient_rejection_is_failure_even_if_response_has_message(self):
        response = {
            'SMSMessageData': {
                'Message': 'Sent to 0/1',
                'Recipients': [{'statusCode': 403, 'status': 'InvalidPhoneNumber', 'number': '+254000000000'}],
            }
        }
        ok, detail = notify._sms_response_status(response)
        self.assertFalse(ok)
        self.assertIn('403', detail)

    def test_missing_recipient_status_is_not_treated_as_success(self):
        self.assertFalse(notify._sms_response_status({'SMSMessageData': {'Message': 'Request accepted'}})[0])

    def test_unexpected_response_is_not_treated_as_success(self):
        self.assertFalse(notify._sms_response_status(None)[0])

    def test_mixed_recipient_results_fail(self):
        response = {
            'SMSMessageData': {
                'Recipients': [
                    {'statusCode': 101, 'status': 'Success', 'number': '+254700000000'},
                    {'statusCode': 405, 'status': 'InsufficientBalance', 'number': '+254711111111'},
                ]
            }
        }
        ok, detail = notify._sms_response_status(response)
        self.assertFalse(ok)
        self.assertIn('405', detail)


if __name__ == '__main__':
    unittest.main()
