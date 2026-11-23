"""Classes, inheritance and dynamic dispatch."""

import math


class Shape:
    def __init__(self, name: str) -> None:
        self.name = name

    def area(self) -> float:
        return 0.0

    def describe(self) -> str:
        return f"{self.name} with area {round(self.area(), 2)}"


class Circle(Shape):
    def __init__(self, radius: float) -> None:
        super().__init__("circle")
        self.radius = radius

    def area(self) -> float:
        return math.pi * self.radius**2


class Rect(Shape):
    def __init__(self, width: float, height: float) -> None:
        super().__init__("rect")
        self.width = width
        self.height = height

    def area(self) -> float:
        return self.width * self.height


class Square(Rect):
    def __init__(self, side: float) -> None:
        super().__init__(side, side)
        self.name = "square"


def total_area(shapes: list[Shape]) -> float:
    total = 0.0
    for shape in shapes:
        total += shape.area()
    return total
