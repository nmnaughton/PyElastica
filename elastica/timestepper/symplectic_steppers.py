__doc__ = """Symplectic time steppers and concepts for integrating the kinematic and dynamic equations of rod-like objects.  """

from typing import Callable, Any, Final

from itertools import zip_longest

from elastica.typing import (
    SystemCollectionType,
    # StepOperatorType,
    # PrefactorOperatorType,
    OperatorType,
    SteppersOperatorsType,
)

import numpy as np

from elastica.rod.data_structures import (
    overload_operator_kinematic_numba,
    overload_operator_dynamic_numba,
)
from elastica.systems.protocol import SymplecticSystemProtocol
from .protocol import SymplecticStepperProtocol
from .tag import StepperTags, SymplecticStepperTag

"""
Developer Note
--------------

For the reasons why we define Mixin classes here, the developer
is referred to the same section on `explicit_steppers.py`.
"""


class SymplecticStepperMixin:
    def __init__(self: SymplecticStepperProtocol):
        self.steps_and_prefactors: Final = self.step_methods()

    def step_methods(self: SymplecticStepperProtocol) -> SteppersOperatorsType:
        # Let the total number of steps for the Symplectic method
        # be (2*n + 1) (for time-symmetry). What we do is collect
        # the first n + 1 entries down in _steps and _prefac below, and then
        # reverse and append it to itself.
        _steps: list[OperatorType] = self.get_steps()
        _steps = _steps + _steps[-2::-1]

        # Prefac here is necessary because the linear-exponential integrator
        # needs only the prefactor and not the dt.
        _prefactors: list[OperatorType] = self.get_prefactors()
        _prefactors = _prefactors + _prefactors[-2::-1]
        assert len(_steps) == 2 * len(_prefactors) - 1

        # Separate the kinematic and dynamic steps
        _kinematic_steps: list[OperatorType] = _steps[::2]
        _dynamic_steps: list[OperatorType] = _steps[1::2]

        def no_operation(*args: Any) -> None:
            pass

        return tuple(
            zip_longest(
                _prefactors,
                _kinematic_steps,
                _dynamic_steps,
                fillvalue=no_operation,
            )
        )

    @property
    def n_stages(self: SymplecticStepperProtocol) -> int:
        return len(self.steps_and_prefactors)

    def step(
        self: SymplecticStepperProtocol,
        SystemCollection: SystemCollectionType,
        time: np.floating,
        dt: np.floating,
    ) -> np.floating:
        return SymplecticStepperMixin.do_step(self, self.steps_and_prefactors, SystemCollection, time, dt)  # type: ignore[attr-defined]

    # TODO: Merge with .step method in the future.
    # DEPRECATED: Use .step instead.
    @staticmethod
    def do_step(
        TimeStepper: SymplecticStepperProtocol,
        steps_and_prefactors: SteppersOperatorsType,
        SystemCollection: SystemCollectionType,
        time: np.floating,
        dt: np.floating,
    ) -> np.floating:
        """
        Function for doing symplectic stepper over the user defined rods (system).

        Returns
        -------
        time: float
            The time after the integration step.

        """
        for kin_prefactor, kin_step, dyn_step in steps_and_prefactors[:-1]:

            for system in SystemCollection._memory_blocks:
                kin_step(TimeStepper, system, time, dt)

            time += kin_prefactor(TimeStepper, dt)

            # Constrain only values
            SystemCollection.constrain_values(time)

            # We need internal forces and torques because they are used by interaction module.
            for system in SystemCollection._memory_blocks:
                system.update_internal_forces_and_torques(time)
                # system.update_internal_forces_and_torques()

            # Add external forces, controls etc.
            SystemCollection.synchronize(time)

            for system in SystemCollection._memory_blocks:
                dyn_step(TimeStepper, system, time, dt)

            # Constrain only rates
            SystemCollection.constrain_rates(time)

        # Peel the last kinematic step and prefactor alone
        last_kin_prefactor = steps_and_prefactors[-1][0]
        last_kin_step = steps_and_prefactors[-1][1]

        for system in SystemCollection._memory_blocks:
            last_kin_step(TimeStepper, system, time, dt)
        time += last_kin_prefactor(TimeStepper, dt)
        SystemCollection.constrain_values(time)

        # Call back function, will call the user defined call back functions and store data
        SystemCollection.apply_callbacks(time, round(time / dt))

        # Zero out the external forces and torques
        for system in SystemCollection._memory_blocks:
            system.reset_external_forces_and_torques(time)

        return time


class PositionVerlet(SymplecticStepperMixin):
    """
    Position Verlet symplectic time stepper class, which
    includes methods for second-order position Verlet.
    """

    Tag: StepperTags = SymplecticStepperTag

    def get_steps(self) -> list[OperatorType]:
        return [
            self._first_kinematic_step,
            self._first_dynamic_step,
        ]

    def get_prefactors(self) -> list[OperatorType]:
        return [
            self._first_prefactor,
        ]

    def _first_prefactor(self, dt: np.floating) -> np.floating:
        return 0.5 * dt

    def _first_kinematic_step(
        self, System: SymplecticSystemProtocol, time: np.floating, dt: np.floating
    ) -> None:
        prefac = self._first_prefactor(dt)
        overload_operator_kinematic_numba(
            System.n_nodes,
            prefac,
            System.kinematic_states.position_collection,
            System.kinematic_states.director_collection,
            System.velocity_collection,
            System.omega_collection,
        )

    def _first_dynamic_step(
        self, System: SymplecticSystemProtocol, time: np.floating, dt: np.floating
    ) -> None:
        overload_operator_dynamic_numba(
            System.dynamic_states.rate_collection,
            System.dynamic_rates(time, dt),
        )


class PEFRL(SymplecticStepperMixin):
    """
    Position Extended Forest-Ruth Like Algorithm of
    I.M. Omelyan, I.M. Mryglod and R. Folk, Computer Physics Communications 146, 188 (2002),
    http://arxiv.org/abs/cond-mat/0110585
    """

    Tag: StepperTags = SymplecticStepperTag

    # xi and chi are confusing, but be careful!
    ξ: np.float64 = np.float64(0.1786178958448091e0)  # ξ
    λ: np.float64 = -np.float64(0.2123418310626054e0)  # λ
    χ: np.float64 = -np.float64(0.6626458266981849e-1)  # χ

    # Pre-calculate other coefficients
    lambda_dash_coeff: np.float64 = 0.5 * (1.0 - 2.0 * λ)
    xi_chi_dash_coeff: np.float64 = 1.0 - 2.0 * (ξ + χ)

    def get_steps(self) -> list[OperatorType]:
        return [
            self._first_kinematic_step,
            self._first_dynamic_step,
            self._second_kinematic_step,
            self._second_dynamic_step,
            self._third_kinematic_step,
        ]

    def get_prefactors(self) -> list[OperatorType]:
        return [
            self._first_kinematic_prefactor,
            self._second_kinematic_prefactor,
            self._third_kinematic_prefactor,
        ]

    def _first_kinematic_prefactor(self, dt: np.floating) -> np.floating:
        return self.ξ * dt

    def _first_kinematic_step(
        self, System: SymplecticSystemProtocol, time: np.floating, dt: np.floating
    ) -> None:
        prefac = self._first_kinematic_prefactor(dt)
        overload_operator_kinematic_numba(
            System.n_nodes,
            prefac,
            System.kinematic_states.position_collection,
            System.kinematic_states.director_collection,
            System.velocity_collection,
            System.omega_collection,
        )
        # System.kinematic_states += prefac * System.kinematic_rates(time, prefac)

    def _first_dynamic_step(
        self, System: SymplecticSystemProtocol, time: np.floating, dt: np.floating
    ) -> None:
        prefac = self.lambda_dash_coeff * dt
        overload_operator_dynamic_numba(
            System.dynamic_states.rate_collection,
            System.dynamic_rates(time, prefac),
        )
        # System.dynamic_states += prefac * System.dynamic_rates(time, prefac)

    def _second_kinematic_prefactor(self, dt: np.floating) -> np.floating:
        return self.χ * dt

    def _second_kinematic_step(
        self, System: SymplecticSystemProtocol, time: np.floating, dt: np.floating
    ) -> None:
        prefac = self._second_kinematic_prefactor(dt)
        overload_operator_kinematic_numba(
            System.n_nodes,
            prefac,
            System.kinematic_states.position_collection,
            System.kinematic_states.director_collection,
            System.velocity_collection,
            System.omega_collection,
        )
        # System.kinematic_states += prefac * System.kinematic_rates(time, prefac)

    def _second_dynamic_step(
        self, System: SymplecticSystemProtocol, time: np.floating, dt: np.floating
    ) -> None:
        prefac = self.λ * dt
        overload_operator_dynamic_numba(
            System.dynamic_states.rate_collection,
            System.dynamic_rates(time, prefac),
        )
        # System.dynamic_states += prefac * System.dynamic_rates(time, prefac)

    def _third_kinematic_prefactor(self, dt: np.floating) -> np.floating:
        return self.xi_chi_dash_coeff * dt

    def _third_kinematic_step(
        self, System: SymplecticSystemProtocol, time: np.floating, dt: np.floating
    ) -> None:
        prefac = self._third_kinematic_prefactor(dt)
        # Need to fill in
        overload_operator_kinematic_numba(
            System.n_nodes,
            prefac,
            System.kinematic_states.position_collection,
            System.kinematic_states.director_collection,
            System.velocity_collection,
            System.omega_collection,
        )
        # System.kinematic_states += prefac * System.kinematic_rates(time, prefac)
