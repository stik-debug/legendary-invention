import unittest
from governance import calculate_vote_result

class VoteRuleTests(unittest.TestCase):
    def test_quorum_failure_overrides_yes_majority(self):
        result = calculate_vote_result(20, 5, 5, 0, 0, quorum_pct=50, approval_threshold_pct=60)
        self.assertEqual(result["result"], "NO_QUORUM")
        self.assertEqual(result["quorum_needed"], 10)

    def test_abstentions_count_toward_quorum_not_approval_denominator(self):
        result = calculate_vote_result(10, 8, 4, 2, 2, quorum_pct=70, approval_threshold_pct=60)
        self.assertEqual(result["result"], "PASSED")
        self.assertEqual(result["yes_needed"], 4)

    def test_failed_threshold_is_rejected(self):
        result = calculate_vote_result(10, 8, 3, 5, 0, quorum_pct=70, approval_threshold_pct=60)
        self.assertEqual(result["result"], "REJECTED")

    def test_empty_yes_no_is_not_approved(self):
        result = calculate_vote_result(10, 8, 0, 0, 8, quorum_pct=70, approval_threshold_pct=50)
        self.assertEqual(result["result"], "REJECTED")

    def test_percentages_are_clamped(self):
        result = calculate_vote_result(10, 10, 10, 0, 0, quorum_pct=0, approval_threshold_pct=200)
        self.assertEqual(result["quorum_pct"], 1)
        self.assertEqual(result["approval_threshold_pct"], 100)
        self.assertEqual(result["result"], "PASSED")

if __name__ == "__main__":
    unittest.main()
