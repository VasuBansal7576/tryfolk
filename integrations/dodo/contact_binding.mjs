// Private inputs are compared here. Only booleans leave this module.
const synthetic = value => typeof value === 'string' && /^synthetic_[A-Za-z0-9_-]+@tryfolk\.invalid$/.test(value);
const nonempty = value => typeof value === 'string' && value.length > 0;
export function contactBindingFacts(payment, expected) {
  const customer = payment?.customer;
  const cart = payment?.product_cart;
  const metadataKeys = ['run_id','account_id','persona_id','fixture_id','target_revision','order_id','product_id'];
  const current = expected.source_account_ownership_verified === true && !!payment && payment.payment_provider === 'dodo' && payment.status === 'requires_payment_method' &&
    ['payment_id','checkout_session_id'].every(key => nonempty(expected[key]) && payment[key] === expected[key]) &&
    nonempty(expected.customer_id) && customer?.customer_id === expected.customer_id &&
    metadataKeys.every(key => nonempty(expected.metadata?.[key]) && payment.metadata?.[key] === expected.metadata[key]) &&
    payment.total_amount === expected.amount_minor && payment.currency === expected.currency &&
    Array.isArray(cart) && cart.length === 1 && cart[0].product_id === expected.product_id && cart[0].quantity === 1 &&
    !payment.subscription_id && !payment.subscription_ids?.length;
  const result = {
    current_payment_customer_order_bound: Boolean(current),
    source_account_ownership_verified: expected.source_account_ownership_verified === true,
    native_email_observed: expected.email !== null && expected.email !== undefined,
    native_email_synthetic_format: synthetic(expected.email),
    provider_email_synthetic_format: synthetic(customer?.email),
    native_email_matches_current_provider: synthetic(expected.email) && customer?.email === expected.email,
  };
  const optionalMatch = !result.native_email_observed || result.native_email_matches_current_provider;
  return {...result, native_candidate_matches_provider_if_present: optionalMatch,
    contact_binding_passed: current && result.provider_email_synthetic_format && optionalMatch};
}

// Server-process memory only. This is never an API/evidence/model result.
export function privateContactExpectation(payment, expected) {
  const facts = contactBindingFacts(payment, expected);
  return {facts, expected_email: facts.contact_binding_passed ? payment.customer.email : null};
}
