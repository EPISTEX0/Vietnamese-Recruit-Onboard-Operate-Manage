/**
 * The permission primitives every FE surface funnels through.
 *
 * These are written as properties over `USER_ROLES` rather than as a hand-kept
 * list of cases, so adding a fourth role to the union makes the suite fail
 * until the new role has a home path and has been considered by every rule
 * below. A per-role checklist would silently keep passing.
 *
 * The incident these guard against: `system_admin` and `hr` were collapsed
 * behind one `isAdmin` boolean, which stranded every staff account on the
 * employee home.
 */
import { describe, it, expect } from "vitest";

import {
  USER_ROLES,
  STAFF_ROLES,
  homePathForRole,
  hasRole,
  isUserRole,
  type UserRole,
} from "./roles";

describe("USER_ROLES", () => {
  it("has no duplicate members", () => {
    expect(new Set(USER_ROLES).size).toBe(USER_ROLES.length);
  });

  it("keeps system_admin and hr as separate roles", () => {
    // The whole point of the union. If these ever collapse, every rule below
    // becomes meaningless.
    expect(USER_ROLES).toContain("system_admin");
    expect(USER_ROLES).toContain("hr");
    expect(USER_ROLES).not.toContain("admin");
  });
});

describe("STAFF_ROLES", () => {
  it("is a subset of USER_ROLES", () => {
    for (const role of STAFF_ROLES) {
      expect(USER_ROLES as readonly string[]).toContain(role);
    }
  });

  it("excludes the self-service role — staff accounts are not minted as `user`", () => {
    expect(STAFF_ROLES as readonly string[]).not.toContain("user");
  });
});

describe("homePathForRole", () => {
  it("is total over the union — every role gets an absolute path", () => {
    for (const role of USER_ROLES) {
      const path = homePathForRole(role);
      expect(path, `no home path for role "${role}"`).toBeTruthy();
      expect(path.startsWith("/"), `home path for "${role}" is not absolute`).toBe(true);
    }
  });

  it("sends no two roles to the same landing page", () => {
    // The regression: merging the staff roles gave them a shared home and
    // handed one of them a console it has no business on.
    const paths = USER_ROLES.map(homePathForRole);
    expect(new Set(paths).size).toBe(USER_ROLES.length);
  });

  it("never lands a staff role on the employee self-service home", () => {
    const employeeHome = homePathForRole("user");
    for (const role of STAFF_ROLES) {
      expect(homePathForRole(role), `${role} was stranded on ${employeeHome}`).not.toBe(
        employeeHome,
      );
    }
  });

  it("pins the routes the app actually links to", () => {
    // Independent restatement of the routing contract: these three paths are
    // hardcoded in the route groups (`(system-admin)`, `(dashboard)`,
    // `(employee)`), so a change here has to be a deliberate one.
    expect(homePathForRole("system_admin")).toBe("/settings");
    expect(homePathForRole("hr")).toBe("/dashboard");
    expect(homePathForRole("user")).toBe("/employee");
  });
});

describe("isUserRole", () => {
  it("accepts every member of the union", () => {
    for (const role of USER_ROLES) {
      expect(isUserRole(role), `rejected known role "${role}"`).toBe(true);
    }
  });

  it("rejects near-miss strings a BE payload could plausibly carry", () => {
    const nearMisses = [
      "admin",
      "Admin",
      "ADMIN",
      "HR",
      "Hr",
      "system-admin",
      "systemadmin",
      "system_admin ",
      " hr",
      "superuser",
      "owner",
      "",
    ];
    for (const value of nearMisses) {
      expect(isUserRole(value), `accepted unknown role ${JSON.stringify(value)}`).toBe(false);
    }
  });

  it("rejects non-strings without throwing", () => {
    const nonStrings = [null, undefined, 0, 1, true, false, {}, [], ["hr"], NaN];
    for (const value of nonStrings) {
      expect(isUserRole(value), `accepted ${JSON.stringify(value) ?? String(value)}`).toBe(false);
    }
  });

  it("does not inherit Array.prototype members as roles", () => {
    // `includes` on the frozen tuple must not be talked into matching e.g.
    // "length" or a prototype key.
    for (const value of ["length", "constructor", "toString", "0"]) {
      expect(isUserRole(value)).toBe(false);
    }
  });
});

describe("hasRole", () => {
  it("grants a role only against an allow-list naming it", () => {
    // Full cross product: for every (holder, allowed) pair the answer must be
    // exactly `holder === allowed`. No pair may leak.
    for (const holder of USER_ROLES) {
      for (const allowed of USER_ROLES) {
        expect(
          hasRole(holder, [allowed]),
          `hasRole("${holder}", ["${allowed}"]) was wrong`,
        ).toBe(holder === allowed);
      }
    }
  });

  it("grants when the role appears anywhere in a multi-role allow-list", () => {
    expect(hasRole("hr", STAFF_ROLES)).toBe(true);
    expect(hasRole("system_admin", STAFF_ROLES)).toBe(true);
    expect(hasRole("user", STAFF_ROLES)).toBe(false);
  });

  it("denies everything when the allow-list is empty", () => {
    for (const role of USER_ROLES) {
      expect(hasRole(role, [])).toBe(false);
    }
  });

  it("denies a null or undefined role even against the widest allow-list", () => {
    // A broken session must not be treated as a wildcard.
    expect(hasRole(null, USER_ROLES)).toBe(false);
    expect(hasRole(undefined, USER_ROLES)).toBe(false);
  });

  it("denies a role the union does not contain", () => {
    // Runtime shape of a BE payload that drifted ahead of this build. The cast
    // is the point: `hasRole` is the last line of defence when the type system
    // has already been bypassed at the network boundary.
    const drifted = "admin" as UserRole;
    expect(hasRole(drifted, USER_ROLES)).toBe(false);
    expect(hasRole(drifted, STAFF_ROLES)).toBe(false);
  });
});
