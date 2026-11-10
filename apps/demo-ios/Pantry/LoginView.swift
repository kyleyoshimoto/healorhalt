import SwiftUI

struct LoginView: View {
    @Environment(Store.self) private var store
    @State private var username = ""
    @State private var password = ""
    @State private var error: String?

    var body: some View {
        VStack(spacing: 16) {
            Text("Pantry")
                .font(.largeTitle.bold())
                .accessibilityIdentifier("login.title")
            if Mutation.isActive("M08") {
                // M08 (B4): the fields are wrapped in a new accessibility container.
                VStack(spacing: 16) { credentialFields }
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier("login.credentials")
            } else {
                credentialFields
            }
            if let error {
                Text(error)
                    .foregroundStyle(.red)
                    .accessibilityIdentifier("login.error")
            }
            Button("Sign in") {
                if !store.logIn(username: username, password: password) {
                    error = "Incorrect username or password"
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(username.isEmpty || password.isEmpty)
            .accessibilityIdentifier(Mutation.pick("M01", "login.signIn", else: "login.submit"))
        }
        .padding(32)
    }

    @ViewBuilder private var credentialFields: some View {
        TextField("Username", text: $username)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .textFieldStyle(.roundedBorder)
            .accessibilityIdentifier("login.username")
        // .oneTimeCode keeps iOS from offering "Save Password?", which would cover the app
        // after every sign-in during tests.
        SecureField("Password", text: $password)
            .textContentType(.oneTimeCode)
            .textFieldStyle(.roundedBorder)
            .accessibilityIdentifier("login.password")
    }
}
