"""Application service for bulk Excel employee import.

Orchestrates parsing of Excel files, auto-creation of departments and
positions referenced in the file, and upsert of employee records
matched by email address.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.modules.employee.domain.entities import Department, Employee, Position
from src.modules.employee.infrastructure.excel_parser import parse_excel

if TYPE_CHECKING:
    from src.modules.employee.infrastructure.department_repository import (
        DepartmentRepository,
    )
    from src.modules.employee.infrastructure.employee_repository import (
        EmployeeRepository,
    )
    from src.modules.employee.infrastructure.position_repository import (
        PositionRepository,
    )


class ImportService:
    """Handles bulk employee import from Excel files.

    Parses .xlsx file bytes, auto-creates departments and positions that
    don't exist yet, and performs upsert (create or update) of employees
    matched by email address. This allows HR to import a single Excel file
    without needing to manually set up departments/positions first.

    Args:
        employee_repository: Repository for employee persistence.
        department_repository: Repository for department lookups and creation.
        position_repository: Repository for position lookups and creation.
    """

    def __init__(
        self,
        employee_repository: EmployeeRepository,
        department_repository: DepartmentRepository,
        position_repository: PositionRepository,
    ) -> None:
        """Initialize ImportService with required repositories.

        Args:
            employee_repository: Repository for employee CRUD operations.
            department_repository: Repository for department name lookups.
            position_repository: Repository for position name lookups.
        """
        self._employee_repo = employee_repository
        self._department_repo = department_repository
        self._position_repo = position_repository

    async def import_from_excel(self, file_bytes: bytes) -> dict[str, Any]:
        """Import employees from an Excel file.

        Parses the Excel file, auto-creates any departments/positions that
        don't exist yet, and upserts employees matched by email.

        Args:
            file_bytes: Raw bytes of the .xlsx file to import.

        Returns:
            A dict matching the ImportResult schema with keys:
            - total_rows: Total number of data rows processed.
            - success_count: Number of rows successfully imported.
            - error_count: Number of rows that failed.
            - errors: List of dicts with 'row' and 'message' keys.
            - departments_created: Number of new departments auto-created.
            - positions_created: Number of new positions auto-created.
        """
        # Parse the Excel file
        parsed_rows, parse_errors = parse_excel(file_bytes)

        total_rows = len(parsed_rows) + len(parse_errors)
        success_count = 0
        errors: list[dict[str, Any]] = list(parse_errors)

        # Track auto-created entities
        self._departments_created = 0
        self._positions_created = 0

        # Cache to avoid duplicate lookups/creates within same import
        self._dept_cache: dict[str, object] = {}
        self._pos_cache: dict[str, object] = {}

        for row_data in parsed_rows:
            row_errors = await self._process_row(row_data)

            if row_errors:
                errors.extend(row_errors)
            else:
                success_count += 1

        error_count = len(errors)

        return {
            "total_rows": total_rows,
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors,
            "departments_created": self._departments_created,
            "positions_created": self._positions_created,
        }

    async def _process_row(self, row_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Process a single parsed row: auto-create references and upsert.

        If a department_name or position_name doesn't exist in the system,
        it will be automatically created. This removes the need for HR to
        manually set up departments/positions before importing.

        Args:
            row_data: Dictionary of parsed row fields from the Excel parser.

        Returns:
            A list of error dicts (empty if the row was processed successfully).
        """
        department_id = None
        position_id = None

        # Resolve or auto-create department (with cache)
        department_name = row_data.get("department_name")
        if department_name:
            cache_key = department_name.strip().lower()
            if cache_key in self._dept_cache:
                department_id = self._dept_cache[cache_key]
            else:
                department = await self._department_repo.get_by_name(department_name)
                if department is None:
                    # Auto-create the department
                    new_dept = Department(name=department_name.strip())
                    department = await self._department_repo.create(new_dept)
                    self._departments_created += 1
                department_id = department.id
                self._dept_cache[cache_key] = department_id

        # Resolve or auto-create position (with cache)
        position_name = row_data.get("position_name")
        if position_name:
            cache_key = position_name.strip().lower()
            if cache_key in self._pos_cache:
                position_id = self._pos_cache[cache_key]
            else:
                position = await self._position_repo.get_by_name(position_name)
                if position is None:
                    # Auto-create the position (linked to department if available)
                    new_pos = Position(name=position_name.strip(), department_id=department_id)
                    position = await self._position_repo.create(new_pos)
                    self._positions_created += 1
                position_id = position.id
                self._pos_cache[cache_key] = position_id

        # Build employee data dict (exclude department_name, position_name)
        email = row_data["email"]
        employee_data = {
            "full_name": row_data["full_name"],
            "email": email,
            "phone": row_data.get("phone"),
            "date_of_birth": row_data.get("date_of_birth"),
            "gender": row_data.get("gender"),
            "address": row_data.get("address"),
            "department_id": department_id,
            "position_id": position_id,
            "start_date": row_data.get("start_date"),
            "id_number": row_data.get("id_number"),
            "tax_code": row_data.get("tax_code"),
            "contract_type": row_data.get("contract_type"),
        }

        # Upsert: check if employee with this email already exists
        existing = await self._employee_repo.get_by_email(email)

        if existing is not None:
            # Update existing employee
            update_fields = {k: v for k, v in employee_data.items() if k != "email"}
            await self._employee_repo.update(existing.id, update_fields)
        else:
            # Create new employee with auto-generated code
            employee_code = await self._employee_repo.get_next_code()
            employee = Employee(
                employee_code=employee_code,
                full_name=str(employee_data["full_name"]),
                email=str(employee_data["email"]),
                phone=row_data.get("phone"),
                date_of_birth=row_data.get("date_of_birth"),
                gender=row_data.get("gender"),
                address=row_data.get("address"),
                department_id=department_id,
                position_id=position_id,
                start_date=row_data.get("start_date"),
                id_number=row_data.get("id_number"),
                tax_code=row_data.get("tax_code"),
                contract_type=row_data.get("contract_type"),
            )
            await self._employee_repo.create(employee)

        return []
