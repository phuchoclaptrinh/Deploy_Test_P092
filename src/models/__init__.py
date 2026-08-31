"""Domain model contracts for FixIt Agent.

Deliberately empty of re-exports. `src.domain.risk_scoring` imports
`src.models.enums`, and `src.models.agent_schemas` imports
`src.domain.risk_scoring`; re-exporting the schemas from this package made
importing *any* enum pull the whole chain in and depend on which module got
there first. Import from the module that defines the name.
"""

__all__: list[str] = []
