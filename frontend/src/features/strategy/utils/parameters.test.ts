import { expect, test } from 'vitest'

import { strategyDefinition, strategyDefaults } from '../../../test/fixtures/strategyDefinition'
import { defaultsFromDefinition, parametersEqual, validateParameters } from './parameters'

test('reads defaults from the backend definition fixture', () => {
  expect(defaultsFromDefinition(strategyDefinition)).toEqual(strategyDefaults)
  expect(parametersEqual(defaultsFromDefinition(strategyDefinition), strategyDefaults)).toBe(true)
})

test('validates integer, minimum, and cross-parameter rules', () => {
  expect(validateParameters(strategyDefinition, { ...strategyDefaults, lookback: 2.5 })).toMatchObject({ lookback: 'Lookback must be a whole number.' })
  expect(validateParameters(strategyDefinition, { ...strategyDefaults, fee_bps: -1 })).toMatchObject({ fee_bps: 'Fee must be at least 0.' })
  expect(validateParameters(strategyDefinition, { ...strategyDefaults, entry_z: 0 })).toMatchObject({ entry_z: 'Entry Z must be greater than 0.' })
  expect(validateParameters(strategyDefinition, { ...strategyDefaults, exit_z: 2 })).toMatchObject({ exit_z: 'Exit Z must be smaller than Entry Z.' })
})
