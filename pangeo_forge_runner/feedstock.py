import ast
from copy import deepcopy
from pathlib import Path
from typing import Optional

from ruamel.yaml import YAML

from .meta_yaml import MetaYaml
from .recipe_rewriter import RecipeRewriter

yaml = YAML()


class Feedstock:
    """
    A Pangeo Forge feedstock
    """

    def __init__(
        self,
        feedstock_dir: Path,
        prune: bool = False,
        callable_args_injections: Optional[dict] = None,
    ):
        """
        feedstock_dir: Path to an existing Feedstock repo
        prune: Set to true if the recipe should be pruned for testing
        callable_args_injections: A dict of callable names (as keys) with injected kwargs as value

        Expects meta.yaml to exist inside in this dir
        """
        self.feedstock_dir = feedstock_dir
        with open(self.feedstock_dir / "meta.yaml") as f:
            self.meta = MetaYaml(**yaml.load(f))

        self.prune = prune
        self.callable_args_injections = (
            callable_args_injections if callable_args_injections else {}
        )

    def _import(self, spec):
        """
        Import & return given object from recipes/ in feedstock_dir

        spec is of form <filename>:<object-name>, similar to what is used
        elsewhere in python.

        Each file is executed only once and cached.
        """
        if not hasattr(self, "_import_cache"):
            self._import_cache = {}

        rewriter = RecipeRewriter(
            prune=self.prune, callable_args_injections=self.callable_args_injections
        )

        module, export = spec.split(":")
        if module not in self._import_cache:
            ns = {**rewriter.get_exec_globals()}
            filename = self.feedstock_dir / f"{module}.py"
            with open(filename) as f:
                # compiling makes debugging easier: https://stackoverflow.com/a/437857
                # Without compiling first, `inspect.getsource()` will not work, and
                # pangeo-forge-recipes uses it to hash recipes!
                recipe_ast = ast.parse(source=f.read(), filename=filename, mode="exec")
                rewritten_ast = rewriter.visit(recipe_ast)
                exec(compile(source=rewritten_ast, filename=filename, mode="exec"), ns)
                self._import_cache[module] = ns

        return self._import_cache[module][export]

    def parse_recipes(self):
        """
        Parse the recipes defined in this feedstock & return them

        *Executes arbitrary code* defined in the feedstock recipes.
        """
        recipes = {}
        for r in self.meta.recipes:
            if {"id", "object"} == set(r.keys()):
                recipes[r["id"]] = self._import(r["object"])
            elif {"dict_object"} == set(r.keys()):
                dict_object = self._import(r["dict_object"])
                for k, v in dict_object.items():
                    recipes[k] = v
            else:
                # MetaYaml schema validation should prevent us from ever hitting this.
                raise ValueError("Could not parse recipes config in meta.yaml")

        return recipes

    def get_expanded_meta(self, drop_none=True) -> dict:
        """
        Return full meta.yaml file, expanding recipes if needed.

        recipes are guaranteed to be a list of dictionaries with 'id' keys.
        'object' keys *may* be present, but not guaranteed.
        """
        meta_copy = deepcopy(self.meta)
        for i, r in enumerate(self.meta.recipes):
            if "dict_object" in r:
                # We have a dict_object, so let's parse the recipes, and provide
                # keep just the ids, discarding the values - as the values do not
                # actually serialize.
                recipes = self.parse_recipes()
                meta_copy.recipes[i] = [
                    # In place of the values, we add a placeholder string, so that the
                    # re-assignment to the MetaYaml schema here will pass validation
                    # of the `recipes` field, which requires "id" and "object" fields.
                    {"id": k, "object": "DICT_VALUE_PLACEHOLDER"}
                    for k, _ in recipes.items()
                ]
        if any([isinstance(r, list) for r in meta_copy.recipes]):
            # if we expanded any dict_objects above, then we'll have some nested lists
            # which we need to flatten here before moving on
            all_aslists = [
                (r if isinstance(r, list) else [r]) for r in meta_copy.recipes
            ]
            meta_copy.recipes = sum(all_aslists, [])

        return (
            # the traitlets MetaYaml schema will give us empty containers
            # by default, but in most cases lets assume we don't want that
            {k: v for k, v in meta_copy.trait_values().items() if v}
            if drop_none
            else meta_copy.trait_values()
        )
