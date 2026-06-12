"""Implementación de hashing de contraseñas con bcrypt."""

import bcrypt

from app.domain.security import IPasswordHasher

# bcrypt solo procesa los primeros 72 bytes del input. El contrato (rechazar contraseñas
# más largas en vez de truncarlas en silencio) se hace cumplir en la capa de validación
# (`RegisterRequest`, ver ADR-22); este recorte es solo un **backstop defensivo** para que
# el primitivo nunca dependa de que el llamador haya validado, ni de la conducta concreta
# (truncar vs. error) de la versión de la librería bcrypt en uso.
_MAX_BCRYPT_BYTES = 72


class BcryptPasswordHasher(IPasswordHasher):
    def hash(self, plain: str) -> str:
        digest = bcrypt.hashpw(self._encode(plain), bcrypt.gensalt())
        return digest.decode("utf-8")

    def verify(self, plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(self._encode(plain), hashed.encode("utf-8"))

    @staticmethod
    def _encode(plain: str) -> bytes:
        return plain.encode("utf-8")[:_MAX_BCRYPT_BYTES]
