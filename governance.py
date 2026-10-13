"""Pure governance rules used by voting workflows and tests."""

def calculate_vote_result(eligible_members, votes_cast, yes_votes, no_votes, abstain_votes,
                          quorum_pct=50, approval_threshold_pct=50):
    """Return deterministic outcome and evidence counts for a closed vote.

    Abstentions count toward participation/quorum but not the YES/NO approval denominator.
    Percentages are clamped to 1..100. A tie passes only when the configured threshold
    permits it (for example, a 50% threshold); groups should set their constitutional rule.
    """
    eligible = max(0, int(eligible_members or 0))
    votes = max(0, int(votes_cast or 0))
    yes = max(0, int(yes_votes or 0))
    no = max(0, int(no_votes or 0))
    abstain = max(0, int(abstain_votes or 0))
    quorum_pct = max(1, min(100, int(50 if quorum_pct is None or quorum_pct == '' else quorum_pct)))
    approval_threshold_pct = max(1, min(100, int(50 if approval_threshold_pct is None or approval_threshold_pct == '' else approval_threshold_pct)))
    quorum_needed = (eligible * quorum_pct + 99) // 100
    yes_no = yes + no
    yes_needed = (yes_no * approval_threshold_pct + 99) // 100
    if votes < quorum_needed:
        result = 'NO_QUORUM'
    elif yes_no == 0:
        result = 'REJECTED'
    elif yes >= yes_needed:
        result = 'PASSED'
    else:
        result = 'REJECTED'
    return {
        'result': result,
        'eligible_members': eligible,
        'votes_cast': votes,
        'yes_votes': yes,
        'no_votes': no,
        'abstain_votes': abstain,
        'quorum_pct': quorum_pct,
        'approval_threshold_pct': approval_threshold_pct,
        'quorum_needed': quorum_needed,
        'yes_needed': yes_needed,
    }
