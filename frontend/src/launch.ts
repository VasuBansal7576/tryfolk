import { quoteSchema } from './contracts.ts';
import { ApiError } from './transport.ts';
import type { createTransport } from './transport.ts';

export const LAUNCH_PERSONA_COUNT = 4;

// Historical records retain their original population. Only new approvals use four.
export function fixedPopulationBlocker(count: number): string | null {
  return count === LAUNCH_PERSONA_COUNT ? null
    : 'New tests use four customers. This historical population remains available for review; it cannot be resized for a new attempt.';
}

export function createLaunchApi(request: ReturnType<typeof createTransport>) {
  return {
    quote: async (app_url: string) => {
      const quote = await request('/quotes', quoteSchema, { body: { app_url, persona_count: LAUNCH_PERSONA_COUNT } });
      if (quote.persona_count !== LAUNCH_PERSONA_COUNT
        || (quote.scenario_plan && (quote.scenario_plan.requested_personas !== LAUNCH_PERSONA_COUNT
          || quote.scenario_plan.assignments.length !== LAUNCH_PERSONA_COUNT
          || new Set(quote.scenario_plan.assignments.map(item => item.persona_index)).size !== LAUNCH_PERSONA_COUNT
          || quote.scenario_plan.assignments.some(item => item.persona_index >= LAUNCH_PERSONA_COUNT)))) {
        throw new ApiError('The returned budget does not cover the four requested customers. Refresh after the frontend and service are updated together.', 200, 'contract_mismatch');
      }
      return quote;
    },
  };
}
