from numpy import allclose, broadcast_to, cross, diag, eye, ndarray, pi, swapaxes, zeros
from numpy.linalg import det
from numpy.random import Generator, default_rng
from pytest import mark

from aiocflib.lighthouse.arrays import normalize, to_float_array
from aiocflib.lighthouse.rotation import (
    matrix_to_rotation_vector,
    orthonormalize,
    rotation_vector_to_matrix,
    skew,
)


def random_rotation_vectors(rng: Generator, angles) -> ndarray:
    axes = normalize(rng.normal(size=(len(angles), 3)))
    return axes * to_float_array(angles)[:, None]


def test_skew_computes_cross_products():
    rng = default_rng(1)
    v, w = rng.normal(size=(2, 10, 3))
    assert allclose(skew(v) @ w[..., None], cross(v, w)[..., None])


def test_rotation_vector_to_matrix_known_values():
    assert allclose(rotation_vector_to_matrix((0, 0, 0)), eye(3))
    assert allclose(
        rotation_vector_to_matrix((0, 0, pi / 2)),
        [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
    )
    assert allclose(
        rotation_vector_to_matrix((pi, 0, 0)), [[1, 0, 0], [0, -1, 0], [0, 0, -1]]
    )


@mark.parametrize(
    "angles,tolerance",
    [
        ([0.0, 1e-12, 1e-8, 1e-6, 2e-6], 1e-15),
        ([0.1, 0.5, 1.0, 2.0, 3.0], 1e-12),
        ([pi - 1e-2, pi - 1e-4, pi - 1e-7], 1e-7),
    ],
    ids=["small", "regular", "near pi"],
)
def test_rotation_vector_round_trip(angles, tolerance):
    rng = default_rng(2)
    vectors = random_rotation_vectors(rng, angles)
    matrices = rotation_vector_to_matrix(vectors)

    assert allclose(matrices @ swapaxes(matrices, -1, -2), eye(3))
    assert allclose(det(matrices), 1.0)
    assert allclose(
        matrix_to_rotation_vector(matrices), vectors, rtol=0, atol=tolerance
    )


def test_rotation_vector_of_half_turn():
    vector = matrix_to_rotation_vector(rotation_vector_to_matrix((0, pi, 0)))
    # Both directions of the axis describe the same rotation
    assert allclose(abs(vector), (0, pi, 0))


def test_conversions_keep_batch_shape():
    vectors = zeros((2, 4, 3))
    matrices = broadcast_to(eye(3), (2, 4, 3, 3))
    assert rotation_vector_to_matrix(vectors).shape == (2, 4, 3, 3)
    assert matrix_to_rotation_vector(matrices).shape == (2, 4, 3)


def test_orthonormalize():
    rng = default_rng(3)
    rotations = rotation_vector_to_matrix(random_rotation_vectors(rng, [0.3, 1.2, 2.9]))
    noisy = rotations + rng.normal(scale=1e-3, size=rotations.shape)

    result = orthonormalize(noisy)
    assert allclose(result @ swapaxes(result, -1, -2), eye(3))
    assert allclose(det(result), 1.0)
    assert allclose(result, rotations, atol=1e-2)

    # Reflections are turned into proper rotations
    assert allclose(det(orthonormalize(diag([1.0, 1.0, -1.0]))), 1.0)
