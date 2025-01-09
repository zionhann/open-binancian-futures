def check_required_params(**kwargs) -> None:
    for name, value in kwargs.items():
        if value is None:
            raise ValueError(f"Parameter '{name}' cannot be None")