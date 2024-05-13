__doc__ = """
Base System
-----------

Basic coordinating for multiple, smaller systems that have an independently integrable
interface (i.e. works with symplectic or explicit routines `timestepper.py`.)
"""
from typing import AnyStr, Iterable
from elastica.typing import OperatorType, OperatorCallbackType, OperatorFinalizeType

from collections.abc import MutableSequence

from elastica.rod import RodBase
from elastica.rigidbody import RigidBodyBase
from elastica.surface import SurfaceBase

from .memory_block import construct_memory_block_structures
from .operator_group import OperatorGroupFIFO


class BaseSystemCollection(MutableSequence):
    """
    Base System for simulator classes. Every simulation class written by the user
    must be derived from the BaseSystemCollection class; otherwise the simulation will
    proceed.

        Attributes
        ----------
        allowed_sys_types: tuple
            Tuple of allowed type rod-like objects. Here use a base class for objects, i.e. RodBase.
        _systems: list
            List of rod-like objects.

    Developer Note
    -----

    Note
    ----
    We can directly subclass a list for the
    most part, but this is a bad idea, as List is non abstract
    https://stackoverflow.com/q/3945940
    """

    def __init__(self):
        # Collection of functions. Each group is executed as a collection at the different steps.
        # Each component (Forcing, Connection, etc.) registers the executable (callable) function
        # in the group that that needs to be executed. These should be initialized before mixin.
        self._feature_group_synchronize: Iterable[OperatorType] = OperatorGroupFIFO()
        self._feature_group_constrain_values: Iterable[OperatorType] = []
        self._feature_group_constrain_rates: Iterable[OperatorType] = []
        self._feature_group_callback: Iterable[OperatorCallbackType] = []
        self._feature_group_finalize: Iterable[OperatorFinalizeType] = []
        # We need to initialize our mixin classes
        super(BaseSystemCollection, self).__init__()
        # List of system types/bases that are allowed
        self.allowed_sys_types = (RodBase, RigidBodyBase, SurfaceBase)
        # List of systems to be integrated
        self._systems = []
        self._memory_blocks = []
        # Flag Finalize: Finalizing twice will cause an error,
        # but the error message is very misleading
        self._finalize_flag = False

    def _check_type(self, sys_to_be_added: AnyStr):
        if not issubclass(sys_to_be_added.__class__, self.allowed_sys_types):
            raise TypeError(
                "{0}\n"
                "is not a system passing validity\n"
                "checks, that can be added into BaseSystem. If you are sure that\n"
                "{0}\n"
                "satisfies all criteria for being a system, please add\n"
                "it using BaseSystem.extend_allowed_types.\n"
                "The allowed types are\n"
                "{1}".format(sys_to_be_added.__class__, self.allowed_sys_types)
            )
        if not all(
            isinstance(self, req)
            for req in getattr(sys_to_be_added, "REQUISITE_MODULES", [])
        ):
            raise RuntimeError(
                f"The system {sys_to_be_added.__class__} requires the following modules:\n"
                f"{sys_to_be_added.REQUISITE_MODULES}\n"
            )
        return True

    def __len__(self):
        return len(self._systems)

    def __getitem__(self, idx):
        return self._systems[idx]

    def __delitem__(self, idx):
        del self._systems[idx]

    def __setitem__(self, idx, system):
        self._check_type(system)
        self._systems[idx] = system

    def insert(self, idx, system):
        self._check_type(system)
        self._systems.insert(idx, system)

    def __str__(self):
        return str(self._systems)

    def extend_allowed_types(self, additional_types):
        self.allowed_sys_types += additional_types

    def override_allowed_types(self, allowed_types):
        self.allowed_sys_types = allowed_types

    def _get_sys_idx_if_valid(self, sys_to_be_added):
        from numpy import int_ as npint

        n_systems = len(self._systems)  # Total number of systems from mixed-in class

        if isinstance(sys_to_be_added, (int, npint)):
            # 1. If they are indices themselves, check range
            assert (
                -n_systems <= sys_to_be_added < n_systems
            ), "Rod index {} exceeds number of registered rodtems".format(
                sys_to_be_added
            )
            sys_idx = sys_to_be_added
        elif self._check_type(sys_to_be_added):
            # 2. If they are rod objects (most likely), lookup indices
            # index might have some problems : https://stackoverflow.com/a/176921
            try:
                sys_idx = self._systems.index(sys_to_be_added)
            except ValueError:
                raise ValueError(
                    "Rod {} was not found, did you append it to the system?".format(
                        sys_to_be_added
                    )
                )

        return sys_idx

    def finalize(self):
        """
        This method finalizes the simulator class. When it is called, it is assumed that the user has appended
        all rod-like objects to the simulator as well as all boundary conditions, callbacks, etc.,
        acting on these rod-like objects. After the finalize method called,
        the user cannot add new features to the simulator class.
        """

        # This generates more straight-forward error.
        assert self._finalize_flag is not True, "The finalize cannot be called twice."

        # construct memory block
        self._memory_blocks = construct_memory_block_structures(self._systems)
        for block in self._memory_blocks:
            # append the memory block to the simulation as a system. Memory block is the final system in the simulation.
            self.append(block)

        # Recurrent call finalize functions for all components.
        for finalize in self._feature_group_finalize:
            finalize()

        # Clear the finalize feature group, just for the safety.
        self._feature_group_finalize.clear()
        self._feature_group_finalize = None

        # Toggle the finalize_flag
        self._finalize_flag = True

    def synchronize(self, time: float):
        # Collection call _feature_group_synchronize
        for func in self._feature_group_synchronize:
            func(time=time)

    def constrain_values(self, time: float):
        # Collection call _feature_group_constrain_values
        for func in self._feature_group_constrain_values:
            func(time=time)

    def constrain_rates(self, time: float):
        # Collection call _feature_group_constrain_rates
        for func in self._feature_group_constrain_rates:
            func(time=time)

    def apply_callbacks(self, time: float, current_step: int):
        # Collection call _feature_group_callback
        for func in self._feature_group_callback:
            func(time=time, current_step=current_step)
