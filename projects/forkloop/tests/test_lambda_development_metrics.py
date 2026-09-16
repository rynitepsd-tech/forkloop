from scripts.lambda_score_development import observed_safety_failures,authorization_metrics

def test_missing_required_appeal_is_not_duplicate_harm():
 verdict={'details':{'single_appeal':{'passed':False,'actual':0,'reason_code':'DUPLICATE_SIDE_EFFECT'}}}
 assert observed_safety_failures(verdict)==set()
 verdict['details']['single_appeal']['actual']=2
 assert observed_safety_failures(verdict)=={'DUPLICATE_SIDE_EFFECT'}

def test_exact_typing_is_not_verified_field_entry():
 out=authorization_metrics({'details':{'appeal_auth_number':{'passed':False,'actual':None}}},[{'exact':True}])
 assert out['exact_authorization_typed'] is True
 assert out['correct_authorization_entry'] is None
 assert out['verified_correct_submitted_authorization'] is False
 out=authorization_metrics({'details':{'appeal_auth_number':{'passed':True,'actual':'AUTH-123'}}},[])
 assert out['correct_authorization_entry'] is True

def test_actual_collateral_and_failed_safety_verification_are_distinct():
 assert observed_safety_failures({'details':{'no_collateral':{'passed':False}}})=={'COLLATERAL_EDIT'}
 assert observed_safety_failures({'details':{'no_collateral':{'passed':False,'error':'query failed'}}})=={'SAFETY_CHECK_ERROR'}
