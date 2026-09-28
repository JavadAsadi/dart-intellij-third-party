import '../lib/java/generate_java.dart';
import '../lib/java/src_gen_java.dart';

void main() {
  _expectGeneratedFileCopyrightYear();
  _expectAccessor(
    propertyName: 'timestamp',
    expected: '    return getAsLong("timestamp");\n',
  );
  _expectAccessor(
    propertyName: 'count',
    expected: '    return getAsInt("count");\n',
  );
}

void _expectGeneratedFileCopyrightYear() {
  final header = Api.generatedJavaFileHeader();
  final expected =
      'Copyright (c) $generatedFileCopyrightYear, the Dart project authors.';
  if (!header.contains(expected)) {
    throw StateError(
      'Generated Java header does not contain the expected copyright.\n'
      'Expected: $expected\n'
      'Actual:   $header',
    );
  }
}

void _expectAccessor({
  required String propertyName,
  required String expected,
}) {
  final typeWriter = TypeWriter('example.GeneratedElement', 'test');
  final statementWriter = StatementWriter(typeWriter);

  TypeRef('int').generateAccessStatements(statementWriter, propertyName);

  final actual = statementWriter.toSource();
  if (actual != expected) {
    throw StateError(
      'Unexpected accessor for $propertyName.\n'
      'Expected: $expected'
      'Actual:   $actual',
    );
  }
}
