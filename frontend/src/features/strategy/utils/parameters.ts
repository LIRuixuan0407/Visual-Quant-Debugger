import type {
  StrategyDefinition,
  StrategyParameterKey,
  StrategyParameters,
} from '../../../types/strategy'

export type ParameterErrors = Partial<Record<StrategyParameterKey, string>>

export function defaultsFromDefinition(definition: StrategyDefinition): StrategyParameters {
  return Object.fromEntries(definition.parameters.map((item) => [item.key, item.default_value]))
}

export function parametersEqual(left: StrategyParameters, right: StrategyParameters): boolean {
  return Object.keys(left).every((key) => (
    left[key] === right[key]
  )) && Object.keys(left).length === Object.keys(right).length
}

export function validateParameters(
  definition: StrategyDefinition,
  parameters: StrategyParameters,
): ParameterErrors {
  const errors: ParameterErrors = {}
  for (const parameter of definition.parameters) {
    const value = parameters[parameter.key]
    if (!Number.isFinite(value)) {
      errors[parameter.key] = `${parameter.label} must be a finite number.`
    } else if (parameter.value_type === 'integer' && !Number.isInteger(value)) {
      errors[parameter.key] = `${parameter.label} must be a whole number.`
    } else if (
      parameter.exclusive_minimum ? value <= parameter.minimum : value < parameter.minimum
    ) {
      errors[parameter.key] = parameter.exclusive_minimum
        ? `${parameter.label} must be greater than ${parameter.minimum}.`
        : `${parameter.label} must be at least ${parameter.minimum}.`
    } else if (parameter.maximum !== null && value > parameter.maximum) {
      errors[parameter.key] = `${parameter.label} must be at most ${parameter.maximum}.`
    }
  }
  for (const rule of definition.validation_rules) {
    if (
      rule.operator === 'less_than'
      && parameters[rule.left_parameter] >= parameters[rule.right_parameter]
    ) {
      errors[rule.left_parameter] = rule.message
    }
  }
  return errors
}
